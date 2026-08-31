"""Stage 4E1 read-only optimization dashboard."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from pc_manager_agent.app.runtime import ApplicationRuntime, SystemOptimizationServices
from pc_manager_agent.confirmation.models import ConfirmationRequest
from pc_manager_agent.domain.optimization_actions import (
    OptimizationActionPreparationResult,
    OptimizationRecommendationReference,
    OptimizationSessionCreateRequest,
    RecommendationActionState,
)
from pc_manager_agent.domain.system_cleanup_execution import SystemCleanupRequest
from pc_manager_agent.domain.system_optimization import OptimizationPlan, SystemOptimizationReport
from pc_manager_agent.reporting.exporter import ReportFormat
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.ui.optimization_review_dialog import OptimizationDomainReviewDialog
from pc_manager_agent.ui.optimization_review_workers import OptimizationReviewWorker
from pc_manager_agent.ui.system_cleanup_dialog import (
    RecycleBinEmptyDialog,
    SystemCleanupDialog,
)


def _bytes_text(value: int | None) -> str:
    if value is None:
        return "不可可靠估算"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return str(value)


class _OptimizationSignals(QObject):
    completed = Signal(object)
    failed = Signal(str)


class _OptimizationWorker(QRunnable):
    """Run the confirmed orchestrator away from the GUI thread."""

    def __init__(self, services: SystemOptimizationServices, plan: OptimizationPlan) -> None:
        super().__init__()
        self.signals = _OptimizationSignals()
        self._services = services
        self._plan = plan
        self._cancellation = CancellationToken()

    def cancel(self) -> None:
        """Request cooperative cancellation of remaining read operations."""
        self._cancellation.cancel()

    @Slot()
    def run(self) -> None:
        """Execute and return a validated report or a user-facing error."""
        try:
            report = self._services.orchestrator.execute(self._plan, self._cancellation)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(report)


class SystemOptimizationTab(QWidget):
    """Present Stage 4E1 reports and open separate controlled Stage 4E2 workflows."""

    status_message = Signal(str)

    def __init__(self, runtime: ApplicationRuntime) -> None:
        super().__init__()
        self._runtime = runtime
        self._services = runtime.create_system_optimization_services()
        self._plan: OptimizationPlan | None = None
        self._confirmation: ConfirmationRequest | None = None
        self._report: SystemOptimizationReport | None = None
        self._worker: _OptimizationWorker | None = None
        self._reviews = runtime.create_optimization_review_services()
        self._review_session_id: UUID | None = None
        self._review_dialog: OptimizationDomainReviewDialog | None = None
        self._review_worker: OptimizationReviewWorker | None = None
        self._closing_reviews = False
        self._build_ui()
        self._load_authorized_roots()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Stage 4E1 先进行只读分析。Stage 4E2 只能对重新验证且再次勾选的对象执行"
            "回收站移动；清空回收站使用独立的不可逆双重确认。两者都不请求管理员权限。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        goal_row = QHBoxLayout()
        self.goal_input = QLineEdit("执行常规健康检查并分析可安全查看的空间占用")
        self.goal_input.setPlaceholderText("例如：诊断电脑变慢并查看可以复查的空间占用")
        self.prepare_button = QPushButton("生成只读计划")
        self.prepare_button.clicked.connect(self.prepare)
        goal_row.addWidget(self.goal_input, 1)
        goal_row.addWidget(self.prepare_button)
        layout.addLayout(goal_row)

        self.root_table = QTableWidget(0, 2)
        self.root_table.setHorizontalHeaderLabels(("纳入个人目录", "已授权目录"))
        self.root_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.root_table.horizontalHeader().setStretchLastSection(True)
        self.root_table.setMaximumHeight(120)
        layout.addWidget(self.root_table)

        self.plan_view = QTextBrowser()
        self.plan_view.setPlaceholderText("计划、风险、范围和限制会显示在这里。")
        self.plan_view.setMaximumHeight(150)
        layout.addWidget(self.plan_view)

        actions = QHBoxLayout()
        self.confirm_button = QPushButton("确认 R0 计划")
        self.run_button = QPushButton("开始只读分析")
        self.cancel_button = QPushButton("取消")
        self.export_button = QPushButton("导出报告")
        self.cleanup_button = QPushButton("对勾选候选进行 Fresh 安全评估")
        self.empty_bin_button = QPushButton("独立检查并清空回收站")
        for button in (
            self.confirm_button,
            self.run_button,
            self.cancel_button,
            self.export_button,
            self.cleanup_button,
            self.empty_bin_button,
        ):
            actions.addWidget(button)
        self.confirm_button.clicked.connect(self.confirm)
        self.run_button.clicked.connect(self.run_analysis)
        self.cancel_button.clicked.connect(self.cancel)
        self.export_button.clicked.connect(self.export_report)
        self.cleanup_button.clicked.connect(self.open_controlled_cleanup)
        self.empty_bin_button.clicked.connect(self.open_recycle_bin_empty)
        self.confirm_button.setEnabled(False)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.cleanup_button.setEnabled(False)
        layout.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.results = QTabWidget()
        self.overview_table = self._table(("项目", "结果"))
        self.storage_table = self._table(("来源", "类别", "已观察大小", "项目数", "状态"))
        self.candidate_table = self._table(
            (
                "选择",
                "类别",
                "来源",
                "已观察",
                "潜在空间",
                "安全分类",
                "保护",
                "置信度",
                "路径",
            )
        )
        self.startup_table = self._table(("名称", "来源", "范围", "启用"))
        self.process_table = self._table(("PID", "名称", "CPU %", "内存 %", "状态"))
        self.finding_table = self._table(("类别", "标题", "置信度", "说明"))
        self.recommendation_table = self._table(
            ("选择", "目标", "建议", "收益", "置信度", "复查入口风险", "复查状态")
        )
        for table, title in (
            (self.overview_table, "概览"),
            (self.storage_table, "存储"),
            (self.candidate_table, "清理候选"),
            (self.startup_table, "启动项"),
            (self.process_table, "进程"),
            (self.finding_table, "性能发现"),
            (self.recommendation_table, "建议"),
        ):
            self.results.addTab(table, title)
        layout.addWidget(self.results, 1)
        review_actions = QHBoxLayout()
        self.create_review_button = QPushButton("将勾选建议加入复查清单")
        self.next_review_button = QPushButton("复查下一条（不执行）")
        self.cancel_review_button = QPushButton("取消剩余复查")
        self.create_review_button.clicked.connect(self.create_review_session)
        self.next_review_button.clicked.connect(self.open_next_review)
        self.cancel_review_button.clicked.connect(self.cancel_review_session)
        for button in (
            self.create_review_button,
            self.next_review_button,
            self.cancel_review_button,
        ):
            review_actions.addWidget(button)
        layout.addLayout(review_actions)
        self.session_view = QTextBrowser()
        self.session_view.setMaximumHeight(130)
        self.session_view.setPlainText(
            "清单不是一键优化授权；每次只复查一条，执行必须使用原业务的独立确认。"
        )
        layout.addWidget(self.session_view)

    @staticmethod
    def _table(headers: tuple[str, ...]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSortingEnabled(True)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _load_authorized_roots(self) -> None:
        roots = self._runtime.authorized_paths.list_authorized()
        self.root_table.setRowCount(len(roots))
        for row, root in enumerate(roots):
            enabled = QTableWidgetItem()
            enabled.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            enabled.setCheckState(Qt.CheckState.Unchecked)
            enabled.setData(Qt.ItemDataRole.UserRole, str(root.path_id))
            self.root_table.setItem(row, 0, enabled)
            self.root_table.setItem(row, 1, QTableWidgetItem(f"{root.label} — {root.path}"))

    def _selected_root_ids(self) -> tuple[UUID, ...]:
        selected: list[UUID] = []
        for row in range(self.root_table.rowCount()):
            item = self.root_table.item(row, 0)
            if item is not None and item.checkState() is Qt.CheckState.Checked:
                selected.append(UUID(str(item.data(Qt.ItemDataRole.UserRole))))
        return tuple(selected)

    @Slot()
    def prepare(self) -> None:
        """Create and display a fresh plan; no system collector runs here."""
        try:
            plan, review = self._services.orchestrator.prepare(
                self.goal_input.text(), self._selected_root_ids()
            )
        except Exception as exc:
            self._show_error(f"无法生成计划：{exc}")
            return
        self._plan = plan
        self._confirmation = None
        self._report = None
        self.run_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.cleanup_button.setEnabled(False)
        self.confirm_button.setEnabled(review.approved)
        issue_text = "无" if review.approved else "；".join(item.message for item in review.issues)
        self.plan_view.setPlainText(
            f"目标：{', '.join(item.value for item in plan.goals)}\n"
            f"工具：{', '.join(item.value for item in plan.tools)}\n"
            f"系统采集器：{', '.join(item.value for item in plan.snapshot_collectors)}\n"
            f"个人授权目录：{len(plan.authorized_roots)} 个\n"
            f"系统已知位置：仅元数据、有限清单；敏感目录和重解析点排除\n"
            f"上限：{plan.max_objects} 个对象，{plan.timeout_seconds:.0f} 秒\n"
            f"风险：R0；系统修改：0；管理员权限：不请求；回滚：NONE（因为不修改）\n"
            f"安全审查：{'通过' if review.approved else '拒绝'}；问题：{issue_text}"
        )
        self.status_message.emit("只读计划已生成；尚未读取系统状态")

    @Slot()
    def confirm(self) -> None:
        """Ask for explicit approval of the exact digest-bound R0 plan."""
        if self._plan is None:
            return
        try:
            request = self._services.orchestrator.request_confirmation(self._plan)
        except Exception as exc:
            self._show_error(f"无法创建确认：{exc}")
            return
        answer = QMessageBox.question(
            self,
            "确认只读分析",
            request.object_summary + "\n\n分析结果和候选不会授权未来清理。是否继续？",
        )
        approved = answer is QMessageBox.StandardButton.Yes
        try:
            self._confirmation = self._services.orchestrator.resolve_confirmation(
                request.confirmation_id, approved, self._plan
            )
        except Exception as exc:
            self._show_error(f"确认无效：{exc}")
            return
        self.run_button.setEnabled(approved)
        self.confirm_button.setEnabled(False)
        self.status_message.emit("只读计划已确认" if approved else "计划已拒绝，未执行")

    @Slot()
    def run_analysis(self) -> None:
        """Start the confirmed orchestration on the shared thread pool."""
        if self._plan is None or self._worker is not None:
            return
        worker = _OptimizationWorker(self._services, self._plan)
        worker.signals.completed.connect(self._completed)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        self.progress.setRange(0, 0)
        self.prepare_button.setEnabled(False)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        QThreadPool.globalInstance().start(worker)
        self.status_message.emit("正在执行有限的本地只读采集；可以取消")

    @Slot()
    def cancel(self) -> None:
        """Request cancellation; no write operation exists to roll back."""
        if self._worker is not None:
            self._worker.cancel()
            self.status_message.emit("已请求取消；正在停止后续只读采集")

    @Slot(object)
    def _completed(self, value: object) -> None:
        self._worker = None
        self.prepare_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        if not isinstance(value, SystemOptimizationReport):
            self._show_error("分析返回了无效报告")
            return
        self._report = value
        self.export_button.setEnabled(True)
        self._populate(value)
        self.cleanup_button.setEnabled(bool(value.cleanup_candidates))
        self.status_message.emit("Stage 4E1 只读分析完成；系统和用户数据修改数量为 0")

    @Slot(str)
    def _failed(self, message: str) -> None:
        self._worker = None
        self.prepare_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self._show_error(f"分析未完成：{message}")

    def _populate(self, report: SystemOptimizationReport) -> None:
        self._fill(
            self.overview_table,
            (
                ("报告 ID", str(report.report_id)),
                ("已观察空间", _bytes_text(report.observed_bytes)),
                ("潜在空间（非承诺）", _bytes_text(report.potential_reclaim_bytes)),
                ("受保护空间", _bytes_text(report.protected_bytes)),
                ("未知空间", _bytes_text(report.unknown_bytes)),
                ("部分来源", str(len(report.partial_sources))),
                ("跳过来源", str(len(report.skipped_sources))),
                ("系统修改", "0（严格只读）"),
            ),
        )
        self._fill(
            self.storage_table,
            tuple(
                (
                    item.source,
                    item.category.value,
                    _bytes_text(item.observed_size_bytes),
                    str(item.item_count),
                    item.availability.value,
                )
                for item in report.snapshot.storage_observations
            ),
        )
        self._populate_candidates(report)
        self._fill(
            self.startup_table,
            tuple(
                (item.name, item.source.value, item.scope.value, str(item.enabled))
                for item in report.snapshot.system.startup_entries
            ),
        )
        processes = report.snapshot.system.processes
        self._fill(
            self.process_table,
            tuple(
                (
                    str(item.pid),
                    item.name,
                    f"{item.cpu_percent:.1f}",
                    f"{item.memory_percent:.1f}",
                    item.status or "",
                )
                for item in (processes.processes if processes else ())
            ),
        )
        self._fill(
            self.finding_table,
            tuple(
                (item.category.value, item.title, item.confidence.value, item.explanation)
                for item in report.performance_findings
            ),
        )
        self._populate_recommendations(report)

    def _populate_recommendations(self, report: SystemOptimizationReport) -> None:
        self.recommendation_table.setSortingEnabled(False)
        self.recommendation_table.setRowCount(len(report.recommendations))
        for row, recommendation in enumerate(report.recommendations):
            selector = QTableWidgetItem()
            selector.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            selector.setCheckState(Qt.CheckState.Unchecked)
            selector.setData(Qt.ItemDataRole.UserRole, str(recommendation.recommendation_id))
            self.recommendation_table.setItem(row, 0, selector)
            try:
                route = self._reviews.router.inspect(
                    OptimizationRecommendationReference(
                        source_report_id=report.report_id,
                        recommendation_id=recommendation.recommendation_id,
                    )
                )
                state = f"{route.actionability.value} → {route.target_domain.value}"
            except Exception:
                state = "STALE / 不可复查；需要新的只读报告"
            values = (
                recommendation.goal.value,
                recommendation.title,
                recommendation.expected_benefit.value,
                recommendation.confidence.value,
                "R0（仅复查）；操作风险由业务重新评估",
                state,
            )
            for column, value in enumerate(values, 1):
                self.recommendation_table.setItem(row, column, QTableWidgetItem(value))
        self.recommendation_table.setSortingEnabled(True)

    @Slot()
    def create_review_session(self) -> None:
        """Record selected IDs only; neither bulk execution nor global Undo is created."""
        if (
            self._report is None
            or self._review_dialog is not None
            or self._review_worker is not None
        ):
            return
        selected = []
        for row in range(self.recommendation_table.rowCount()):
            item = self.recommendation_table.item(row, 0)
            if item is not None and item.checkState() is Qt.CheckState.Checked:
                selected.append(UUID(str(item.data(Qt.ItemDataRole.UserRole))))
        if not selected:
            self._show_error("请明确勾选要逐项复查的建议；默认不选择任何建议。")
            return
        try:
            if self._review_session_id is not None:
                self._reviews.sessions.cancel(self._review_session_id)
            session = self._reviews.sessions.create(
                OptimizationSessionCreateRequest(
                    source_report_id=self._report.report_id, recommendation_ids=tuple(selected)
                )
            )
            self._review_session_id = session.session_id
            self._show_review_session()
        except Exception as exc:
            self._show_error(f"清单未创建：{exc}")

    @Slot()
    def open_next_review(self) -> None:
        """Enter exactly one preparation; never continue automatically after an outcome."""
        if (
            self._review_session_id is None
            or self._review_dialog is not None
            or self._review_worker is not None
        ):
            return
        try:
            session = self._reviews.sessions.get(self._review_session_id)
            pending = next(
                (item for item in session.items if item.state is RecommendationActionState.PENDING),
                None,
            )
            if pending is None:
                self._show_review_session()
                return
            worker = OptimizationReviewWorker(
                self._reviews.sessions, session.session_id, pending.recommendation_id
            )
            worker.signals.completed.connect(self._review_prepared)
            worker.signals.failed.connect(self._review_failed)
            self._review_worker = worker
            QThreadPool.globalInstance().start(worker)
        except Exception as exc:
            self._show_error(f"无法进入复查：{exc}")

    @Slot(object)
    def _review_prepared(self, value: object) -> None:
        self._review_worker = None
        if self._closing_reviews or self._review_session_id is None:
            return
        if not isinstance(value, OptimizationActionPreparationResult):
            self._review_failed("无效的复查结果")
            return
        preparation = value
        try:
            session = self._reviews.sessions.get(self._review_session_id)
            if session.status.value in {"CANCELLED", "STALE"}:
                self._show_review_session()
                return
            if preparation.fresh_context_id is None:
                self._show_review_session()
                return
            context = self._reviews.handoffs.take(
                preparation.fresh_context_id, preparation.route_id
            )
            # Repeat source invalidation immediately before navigation; the domain still
            # performs all target identity, policy, Preview and authorization checks.
            route = self._reviews.router.inspect(
                OptimizationRecommendationReference(
                    source_report_id=session.source_report_id,
                    recommendation_id=preparation.recommendation_id,
                )
            )
            if route.source_digest != context.route.source_digest:
                raise ValueError("建议来源已变化，请重新分析")
            dialog = OptimizationDomainReviewDialog(
                self._runtime, session.session_id, preparation, context, self
            )
            self._review_dialog = dialog
            dialog.finished.connect(lambda _result: self._review_closed(preparation))
            dialog.open()
            self._show_review_session()
        except Exception as exc:
            # A consumed navigation context cannot be retried; stop the remaining list
            # instead of stranding it as ROUTED or silently opening another domain.
            self.cancel_review_session()
            self._show_error(f"无法进入复查：{exc}")

    @Slot(str)
    def _review_failed(self, message: str) -> None:
        self._review_worker = None
        if not self._closing_reviews:
            self._show_error(f"复查未开始：{message}；请检查来源是否过期或目录授权是否变更。")
            self._show_review_session()

    def _review_closed(self, preparation: OptimizationActionPreparationResult) -> None:
        self._review_dialog = None
        if self._review_session_id is None:
            return
        try:
            session = self._reviews.sessions.get(self._review_session_id)
            item = next(
                value
                for value in session.items
                if value.recommendation_id == preparation.recommendation_id
            )
            if item.state is RecommendationActionState.ROUTED:
                self._reviews.sessions.close_review(
                    session.session_id, item.recommendation_id, preparation.handoff_id
                )
            self._show_review_session()
        except Exception as exc:
            self._show_error(f"无法更新清单；不会推断成功：{exc}")

    @Slot()
    def cancel_review_session(self) -> None:
        """Cancel remaining reviews only; completed work and external installers stay intact."""
        if self._review_session_id is not None:
            try:
                self._reviews.sessions.cancel(self._review_session_id)
                self._show_review_session()
            except Exception as exc:
                self._show_error(f"未能保存取消状态：{exc}")

    def _show_review_session(self) -> None:
        if self._review_session_id is None:
            return
        session = self._reviews.sessions.get(self._review_session_id)
        lines = [f"复查清单 {session.session_id} — {session.status.value}"]
        for index, item in enumerate(session.items, 1):
            detail = (
                f"；业务结果 {item.outcome.outcome.value}；恢复 {item.outcome.recovery_level.value}"
                if item.outcome
                else "；没有已验证的执行结果"
            )
            lines.append(f"{index}. {item.state.value}{detail}")
        lines.append("取消只停止后续复查，不自动还原已完成操作。性能收益尚未测量。")
        self.session_view.setPlainText("\n".join(lines))

    @staticmethod
    def _fill(table: QTableWidget, rows: tuple[tuple[str, ...], ...]) -> None:
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, text in enumerate(row):
                table.setItem(row_index, column, QTableWidgetItem(text))
        table.setSortingEnabled(True)
        table.resizeColumnsToContents()

    @Slot()
    def export_report(self) -> None:
        """Export only after an explicit local save choice; existing files are refused."""
        if self._report is None:
            return
        filename, selected = QFileDialog.getSaveFileName(
            self,
            "导出 Stage 4E1 报告",
            "system-optimization-report.json",
            "JSON (*.json);;CSV (*.csv)",
        )
        if not filename:
            return
        format = ReportFormat.CSV if selected.startswith("CSV") else ReportFormat.JSON
        try:
            result = self._services.exporter.export(Path(filename), format, self._report)
        except Exception as exc:
            self._show_error(f"报告未导出：{exc}")
            return
        self.status_message.emit(f"报告已创建：{result.path}；不会覆盖已有文件")

    def _populate_candidates(self, report: SystemOptimizationReport) -> None:
        """Display intent-only candidate checkboxes, all unchecked by default."""
        self.candidate_table.setSortingEnabled(False)
        self.candidate_table.setRowCount(len(report.cleanup_candidates))
        for row, candidate in enumerate(report.cleanup_candidates):
            selector = QTableWidgetItem()
            selector.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            selector.setCheckState(Qt.CheckState.Unchecked)
            selector.setData(Qt.ItemDataRole.UserRole, str(candidate.candidate_id))
            selector.setToolTip("这只是 Stage 4E1 意向选择；不会直接授权清理")
            self.candidate_table.setItem(row, 0, selector)
            values = (
                candidate.category.value,
                candidate.source,
                _bytes_text(candidate.observed_size_bytes),
                _bytes_text(candidate.potential_reclaim_bytes),
                candidate.safety_classification.value,
                candidate.protection_level.value,
                candidate.confidence.value,
                str(candidate.path) if candidate.path else "不提供对象路径",
            )
            for column, text in enumerate(values, start=1):
                self.candidate_table.setItem(row, column, QTableWidgetItem(text))
        self.candidate_table.setSortingEnabled(True)
        self.candidate_table.resizeColumnsToContents()

    def _selected_candidate_ids(self) -> tuple[UUID, ...]:
        selected: list[UUID] = []
        for row in range(self.candidate_table.rowCount()):
            item = self.candidate_table.item(row, 0)
            if item is not None and item.checkState() is Qt.CheckState.Checked:
                selected.append(UUID(str(item.data(Qt.ItemDataRole.UserRole))))
        return tuple(selected)

    @Slot()
    def open_controlled_cleanup(self) -> None:
        """Open a new Fresh workflow; the report selection is intent only."""
        if self._report is None:
            return
        selected = self._selected_candidate_ids()
        if not selected:
            self._show_error("请先勾选至少一个报告候选；默认不会选择任何对象。")
            return
        dialog = SystemCleanupDialog(
            self._runtime,
            SystemCleanupRequest(
                source_report_id=self._report.report_id,
                selected_candidate_ids=selected,
            ),
            self,
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        self.status_message.emit("已开始独立 Fresh 安全评估；旧报告没有执行权限")

    @Slot()
    def open_recycle_bin_empty(self) -> None:
        """Open the independent R2_HIGH_IMPACT exact-volume workflow."""
        dialog = RecycleBinEmptyDialog(self._runtime, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        self.status_message.emit("正在独立检查回收站；尚未授权清空")

    def shutdown(self) -> None:
        """Cancel pending reads during controlled application shutdown."""
        self.cancel()
        self._closing_reviews = True
        self.cancel_review_session()
        if self._review_dialog is not None:
            self._review_dialog.shutdown()

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "操作未执行", message)
        self.status_message.emit(message)
