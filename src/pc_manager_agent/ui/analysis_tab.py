"""Stage 1 PySide6 file-analysis page; business actions stay in services."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import UUID

from PySide6.QtCore import Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from pc_manager_agent.app.runtime import ApplicationRuntime, FileAnalysisServices
from pc_manager_agent.confirmation.models import ConfirmationRequest
from pc_manager_agent.domain.file_analysis import (
    AnalysisMatchMode,
    AnalysisType,
    FileAnalysisFilters,
    FileAnalysisIntentDraft,
    FileAnalysisPlan,
    FileAnalysisProgress,
    FileAnalysisReport,
)
from pc_manager_agent.domain.reports import FileCategory, ScanStatus
from pc_manager_agent.orchestration.explanation import render_analysis_explanation
from pc_manager_agent.orchestration.file_analysis_planner import FileAnalysisPlanningResult
from pc_manager_agent.reporting.exporter import ReportFormat
from pc_manager_agent.ui.workers import (
    ExplanationWorker,
    FileAnalysisWorker,
    PlannerWorker,
    require_analysis_report,
    require_planning_result,
)


class FileAnalysisTab(QWidget):
    """Present authorization, planning, confirmation, progress, and paged results."""

    status_message = Signal(str)
    move_selected_requested = Signal(object)
    rename_selected_requested = Signal(object)

    def __init__(self, runtime: ApplicationRuntime) -> None:
        super().__init__()
        self._runtime = runtime
        self._services: FileAnalysisServices | None = None
        self._plan: FileAnalysisPlan | None = None
        self._confirmation: ConfirmationRequest | None = None
        self._report: FileAnalysisReport | None = None
        self._analysis_worker: FileAnalysisWorker | None = None
        self._planner_worker: PlannerWorker | None = None
        self._explanation_worker: ExplanationWorker | None = None
        self._page_offset = 0
        self._page_size = 200
        self._building = True
        self._build_ui()
        self._building = False
        self.refresh_paths()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        path_grid = QGridLayout()
        path_grid.addWidget(QLabel("已授权目录"), 0, 0)
        path_grid.addWidget(QLabel("自定义禁止目录"), 0, 1)
        self.authorized_list = QListWidget()
        self.authorized_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.forbidden_list = QListWidget()
        path_grid.addWidget(self.authorized_list, 1, 0)
        path_grid.addWidget(self.forbidden_list, 1, 1)
        authorized_actions = QHBoxLayout()
        add_authorized = QPushButton("添加授权目录")
        remove_authorized = QPushButton("移除授权")
        add_authorized.clicked.connect(self._add_authorized)
        remove_authorized.clicked.connect(self._remove_authorized)
        authorized_actions.addWidget(add_authorized)
        authorized_actions.addWidget(remove_authorized)
        forbidden_actions = QHBoxLayout()
        add_forbidden = QPushButton("添加禁止目录")
        remove_forbidden = QPushButton("移除禁止项")
        add_forbidden.clicked.connect(self._add_forbidden)
        remove_forbidden.clicked.connect(self._remove_forbidden)
        forbidden_actions.addWidget(add_forbidden)
        forbidden_actions.addWidget(remove_forbidden)
        path_grid.addLayout(authorized_actions, 2, 0)
        path_grid.addLayout(forbidden_actions, 2, 1)

        goal_row = QHBoxLayout()
        self.goal_input = QLineEdit()
        self.goal_input.setPlaceholderText(
            "例如：找出 Downloads 中大于 500MB 且疑似三个月未使用的文件"
        )
        self.plan_button = QPushButton("生成只读分析计划")
        self.plan_button.clicked.connect(self._start_planning_from_button)
        goal_row.addWidget(self.goal_input)
        goal_row.addWidget(self.plan_button)

        options = QHBoxLayout()
        self.minimum_size_mb = QSpinBox()
        self.minimum_size_mb.setRange(1, 10_000_000)
        self.minimum_size_mb.setValue(1_024)
        self.minimum_size_mb.setSuffix(" MB")
        self.inactive_days = QSpinBox()
        self.inactive_days.setRange(1, 3_650)
        self.inactive_days.setValue(90)
        self.inactive_days.setSuffix(" 天")
        self.large_checkbox = QCheckBox("大文件")
        self.large_checkbox.setChecked(True)
        self.inactive_checkbox = QCheckBox("疑似长期未使用")
        self.inactive_checkbox.setChecked(True)
        self.duplicate_checkbox = QCheckBox("重复文件")
        self.match_mode = QComboBox()
        self.match_mode.addItem("同时满足全部条件", AnalysisMatchMode.ALL.value)
        self.match_mode.addItem("满足任一条件", AnalysisMatchMode.ANY.value)
        options.addWidget(QLabel("大小阈值"))
        options.addWidget(self.minimum_size_mb)
        options.addWidget(QLabel("闲置阈值"))
        options.addWidget(self.inactive_days)
        options.addWidget(self.large_checkbox)
        options.addWidget(self.inactive_checkbox)
        options.addWidget(self.duplicate_checkbox)
        options.addWidget(self.match_mode)

        self.risk_label = QLabel("风险：尚未生成计划")
        self.risk_label.setStyleSheet("font-weight: 600;")
        self.plan_view = QTextBrowser()
        self.plan_view.setMaximumHeight(190)
        plan_actions = QHBoxLayout()
        self.confirm_button = QPushButton("确认计划")
        self.reject_button = QPushButton("拒绝计划")
        self.run_button = QPushButton("开始只读分析")
        self.cancel_button = QPushButton("取消")
        for button in (
            self.confirm_button,
            self.reject_button,
            self.run_button,
            self.cancel_button,
        ):
            button.setEnabled(False)
            plan_actions.addWidget(button)
        self.confirm_button.clicked.connect(self._approve_plan)
        self.reject_button.clicked.connect(self._reject_plan)
        self.run_button.clicked.connect(self._run_analysis)
        self.cancel_button.clicked.connect(self.cancel)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress_label = QLabel("尚未开始")

        filters = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索文件名或路径")
        self.category_filter = QComboBox()
        self.category_filter.addItem("全部类型", None)
        for category in FileCategory:
            self.category_filter.addItem(category.value, category.value)
        self.result_minimum_mb = QSpinBox()
        self.result_minimum_mb.setRange(0, 10_000_000)
        self.result_minimum_mb.setSuffix(" MB")
        self.sort_field = QComboBox()
        for label, value in (
            ("按大小", "size_bytes"),
            ("按名称", "name"),
            ("按修改时间", "modified_at"),
            ("按访问时间", "accessed_at"),
            ("按类型", "category"),
        ):
            self.sort_field.addItem(label, value)
        self.sort_direction = QComboBox()
        self.sort_direction.addItem("降序", True)
        self.sort_direction.addItem("升序", False)
        apply_filter = QPushButton("应用筛选")
        apply_filter.clicked.connect(self._reset_and_load_page)
        filters.addWidget(self.search_input)
        filters.addWidget(self.category_filter)
        filters.addWidget(self.result_minimum_mb)
        filters.addWidget(self.sort_field)
        filters.addWidget(self.sort_direction)
        filters.addWidget(apply_filter)

        self.results_table = QTableWidget(0, 9)
        self.results_table.setHorizontalHeaderLabels(
            (
                "文件名",
                "完整路径",
                "大小（字节）",
                "类型",
                "修改时间",
                "访问时间",
                "状态",
                "可信度",
                "重复组",
            )
        )
        self.results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.results_table.horizontalHeader().setStretchLastSection(True)

        page_actions = QHBoxLayout()
        self.previous_button = QPushButton("上一页")
        self.next_button = QPushButton("下一页")
        self.open_folder_button = QPushButton("打开所在目录")
        self.export_button = QPushButton("导出 CSV/JSON")
        self.explain_button = QPushButton("生成模型说明")
        self.move_selected_button = QPushButton("移动勾选项")
        self.rename_selected_button = QPushButton("重命名勾选项")
        self.previous_button.clicked.connect(self._previous_page)
        self.next_button.clicked.connect(self._next_page)
        self.open_folder_button.clicked.connect(self._open_selected_folder)
        self.export_button.clicked.connect(self._export_report)
        self.explain_button.clicked.connect(self._explain_report)
        self.move_selected_button.clicked.connect(self._request_move_selected)
        self.rename_selected_button.clicked.connect(self._request_rename_selected)
        for button in (
            self.previous_button,
            self.next_button,
            self.open_folder_button,
            self.export_button,
            self.explain_button,
            self.move_selected_button,
            self.rename_selected_button,
        ):
            button.setEnabled(False)
            page_actions.addWidget(button)

        self.summary_view = QTextBrowser()
        self.summary_view.setMaximumHeight(145)
        layout.addLayout(path_grid)
        layout.addLayout(goal_row)
        layout.addLayout(options)
        layout.addWidget(self.risk_label)
        layout.addWidget(self.plan_view)
        layout.addLayout(plan_actions)
        layout.addWidget(self.progress)
        layout.addWidget(self.progress_label)
        layout.addLayout(filters)
        layout.addWidget(self.results_table, 1)
        layout.addLayout(page_actions)
        layout.addWidget(self.summary_view)

        self.goal_input.textChanged.connect(self._invalidate_plan)
        self.authorized_list.itemSelectionChanged.connect(self._invalidate_plan)
        self.minimum_size_mb.valueChanged.connect(self._invalidate_plan)
        self.inactive_days.valueChanged.connect(self._invalidate_plan)
        self.large_checkbox.toggled.connect(self._invalidate_plan)
        self.inactive_checkbox.toggled.connect(self._invalidate_plan)
        self.duplicate_checkbox.toggled.connect(self._invalidate_plan)
        self.match_mode.currentIndexChanged.connect(self._invalidate_plan)

    @Slot()
    def refresh_paths(self) -> None:
        """Refresh both authorization lists from the local service."""
        self._building = True
        self.authorized_list.clear()
        for record in self._runtime.authorized_paths.list_authorized():
            marker = "★ " if record.favorite else ""
            item = QListWidgetItem(f"{marker}{record.label} — {record.path}")
            item.setData(Qt.ItemDataRole.UserRole, str(record.path_id))
            self.authorized_list.addItem(item)
        if self.authorized_list.count():
            self.authorized_list.item(0).setSelected(True)
        self.forbidden_list.clear()
        for record in self._runtime.authorized_paths.list_forbidden():
            item = QListWidgetItem(f"{record.label} — {record.path}")
            item.setData(Qt.ItemDataRole.UserRole, str(record.path_id))
            self.forbidden_list.addItem(item)
        self._building = False

    @Slot()
    def _add_authorized(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择要授权的本地目录")
        if not selected:
            return
        if (
            QMessageBox.question(
                self,
                "确认授权目录",
                f"允许应用只读扫描以下目录及其安全子目录？\n{selected}",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        favorite = (
            QMessageBox.question(
                self,
                "常用目录",
                "是否将这个目录标记为常用目录？",
            )
            == QMessageBox.StandardButton.Yes
        )
        try:
            self._runtime.authorized_paths.add_authorized(
                Path(selected),
                favorite=favorite,
            )
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.refresh_paths()
        self.status_message.emit("已保存授权目录；没有开始扫描")

    @Slot()
    def _add_forbidden(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择禁止扫描的目录")
        if not selected:
            return
        if (
            QMessageBox.question(
                self,
                "确认禁止目录",
                f"将始终阻止应用扫描以下目录？\n{selected}",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self._runtime.authorized_paths.add_forbidden(Path(selected))
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.refresh_paths()
        self.status_message.emit("已保存自定义禁止目录")

    @Slot()
    def _remove_authorized(self) -> None:
        self._remove_selected(self.authorized_list, "移除这个目录授权？")

    @Slot()
    def _remove_forbidden(self) -> None:
        self._remove_selected(self.forbidden_list, "移除这个自定义禁止项？")

    def _remove_selected(self, widget: QListWidget, prompt: str) -> None:
        item = cast(QListWidgetItem | None, widget.currentItem())
        if item is None:
            self._show_error("请先选择要移除的项目。")
            return
        if QMessageBox.question(self, "确认修改", prompt) != QMessageBox.StandardButton.Yes:
            return
        try:
            self._runtime.authorized_paths.remove(UUID(item.data(Qt.ItemDataRole.UserRole)))
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.refresh_paths()
        self._invalidate_plan()

    @Slot(bool)
    def _start_planning_from_button(self, _checked: bool) -> None:
        """Start planning without forwarding the button's checked state."""
        self.start_planning()

    def start_planning(self, goal: str | None = None) -> None:
        """Create a manual plan or start a consented provider planning worker."""
        if goal is not None:
            self.goal_input.setText(goal)
        user_goal = self.goal_input.text().strip()
        if not user_goal:
            self._show_error("请先输入文件分析目标。")
            return
        root_ids = self._selected_root_ids()
        if not root_ids:
            self._show_error("请先添加并选择至少一个授权目录。")
            return
        try:
            services = self._runtime.create_file_analysis_services()
        except Exception as exc:
            self._show_error(str(exc))
            return
        self._services = services
        self.plan_button.setEnabled(False)
        if services.planner is None:
            try:
                plan = services.compiler.compile(user_goal, self._manual_intent(root_ids))
            except Exception as exc:
                self.plan_button.setEnabled(True)
                self._show_error(str(exc))
                return
            self._accept_planning_result(
                FileAnalysisPlanningResult(
                    plan=plan,
                    provider="deterministic-manual",
                    provider_request_id=None,
                )
            )
            self.status_message.emit("模型未配置，已根据界面阈值生成确定性只读计划")
            return
        try:
            consent = services.planner.request_external_consent(user_goal, root_ids)
            approved = (
                QMessageBox.question(
                    self,
                    "确认外部模型处理",
                    consent.object_summary,
                )
                == QMessageBox.StandardButton.Yes
            )
            self._runtime.external_consent.resolve(consent.confirmation_id, approved)
            if not approved:
                self.plan_button.setEnabled(True)
                self.status_message.emit("未发送任何数据；可以使用手动阈值计划")
                return
            worker = PlannerWorker(
                services.planner,
                user_goal,
                consent.confirmation_id,
                root_ids,
            )
            worker.signals.completed.connect(self._planning_completed)
            worker.signals.failed.connect(self._planning_failed)
            self._planner_worker = worker
            self.status_message.emit("正在请求模型生成结构化意图……")
            QThreadPool.globalInstance().start(worker)
        except Exception as exc:
            self.plan_button.setEnabled(True)
            self._show_error(str(exc))

    def _manual_intent(self, root_ids: tuple[UUID, ...]) -> FileAnalysisIntentDraft:
        analyses: list[AnalysisType] = []
        if self.large_checkbox.isChecked():
            analyses.append(AnalysisType.LARGE_FILES)
        if self.inactive_checkbox.isChecked():
            analyses.append(AnalysisType.INACTIVE_FILES)
        if self.duplicate_checkbox.isChecked():
            analyses.append(AnalysisType.DUPLICATES)
        return FileAnalysisIntentDraft(
            authorized_root_ids=root_ids,
            filters=FileAnalysisFilters(
                minimum_size_bytes=self.minimum_size_mb.value() * 1_048_576,
                inactive_days=self.inactive_days.value(),
            ),
            analyses=tuple(analyses),
            match_mode=AnalysisMatchMode(self.match_mode.currentData()),
        )

    @Slot(object)
    def _planning_completed(self, value: object) -> None:
        self._planner_worker = None
        try:
            result = require_planning_result(value)
        except TypeError as exc:
            self._planning_failed(str(exc))
            return
        self._accept_planning_result(result)

    def _accept_planning_result(self, result: FileAnalysisPlanningResult) -> None:
        if self._services is None:
            self._planning_failed("Planning services are unavailable")
            return
        try:
            review = self._services.orchestrator.review(
                result.plan,
                provider=result.provider,
                provider_request_id=result.provider_request_id,
            )
            if not review.approved:
                details = "\n".join(issue.message for issue in review.issues)
                raise RuntimeError(f"安全审查拒绝计划：\n{details}")
            confirmation = self._services.orchestrator.request_plan_confirmation(result.plan)
        except Exception as exc:
            self._planning_failed(str(exc))
            return
        self._plan = result.plan
        self._confirmation = confirmation
        self.plan_button.setEnabled(True)
        self.plan_view.setPlainText(result.plan.model_dump_json(indent=2))
        self.risk_label.setText("风险：R0 只读；修改文件 0；删除文件 0；计划变更后确认立即失效")
        self.confirm_button.setEnabled(True)
        self.reject_button.setEnabled(True)
        self.run_button.setEnabled(False)
        self.status_message.emit("计划已通过独立安全审查，等待确认")

    @Slot(str)
    def _planning_failed(self, message: str) -> None:
        self._planner_worker = None
        self.plan_button.setEnabled(True)
        self._show_error(f"生成计划失败：{message}")

    @Slot()
    def _approve_plan(self) -> None:
        if self._services is None or self._plan is None or self._confirmation is None:
            return
        try:
            self._services.orchestrator.resolve_plan_confirmation(
                self._confirmation.confirmation_id,
                True,
                self._plan,
            )
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.confirm_button.setEnabled(False)
        self.reject_button.setEnabled(False)
        self.run_button.setEnabled(True)
        self.status_message.emit("计划已确认，可以开始 R0 只读分析")

    @Slot()
    def _reject_plan(self) -> None:
        if self._services is None or self._plan is None or self._confirmation is None:
            return
        try:
            self._services.orchestrator.resolve_plan_confirmation(
                self._confirmation.confirmation_id,
                False,
                self._plan,
            )
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.confirm_button.setEnabled(False)
        self.reject_button.setEnabled(False)
        self.run_button.setEnabled(False)
        self.status_message.emit("计划已拒绝，没有读取文件")

    @Slot()
    def _run_analysis(self) -> None:
        if self._plan is None or self._analysis_worker is not None:
            return
        try:
            worker = FileAnalysisWorker(self._runtime, self._plan)
        except Exception as exc:
            self._show_error(str(exc))
            return
        worker.signals.progress.connect(self._progress_changed)
        worker.signals.completed.connect(self._analysis_completed)
        worker.signals.failed.connect(self._analysis_failed)
        self._analysis_worker = worker
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.progress_label.setText("正在安全遍历授权目录……")
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _progress_changed(self, value: object) -> None:
        if not isinstance(value, FileAnalysisProgress):
            return
        if value.total_units:
            self.progress.setRange(0, value.total_units)
            self.progress.setValue(value.completed_units or 0)
        else:
            self.progress.setRange(0, 0)
        if value.phase == "scanning":
            self.progress_label.setText(
                f"已扫描 {value.files_scanned:,} 个文件、{value.directories_scanned:,} 个目录，"
                f"总计 {value.total_bytes:,} 字节；跳过/错误 {value.errors:,}"
            )
        else:
            self.progress_label.setText(
                f"分析阶段：{value.phase}；进度 {value.completed_units or 0}/"
                f"{value.total_units if value.total_units is not None else '?'}"
            )

    @Slot(object)
    def _analysis_completed(self, value: object) -> None:
        try:
            report = require_analysis_report(value)
        except TypeError as exc:
            self._analysis_failed(str(exc))
            return
        self._analysis_worker = None
        self._report = report
        self.cancel_button.setEnabled(False)
        self.run_button.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress_label.setText(
            "任务已取消并保留部分结果"
            if report.summary.status is ScanStatus.CANCELLED
            else "只读分析完成"
        )
        self.summary_view.setPlainText(render_analysis_explanation(report.summary))
        self._page_offset = 0
        self._load_page()
        self.export_button.setEnabled(True)
        self.explain_button.setEnabled(
            self._services is not None and self._services.explainer is not None
        )
        self.status_message.emit(
            f"{report.summary.status.value}：找到 {report.summary.matching_files:,} 个候选"
        )

    @Slot(str)
    def _analysis_failed(self, message: str) -> None:
        self._analysis_worker = None
        self.cancel_button.setEnabled(False)
        self.run_button.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self._show_error(f"只读分析失败：{message}")

    @Slot()
    def cancel(self) -> None:
        """Request cooperative cancellation without terminating a thread."""
        if self._analysis_worker is not None:
            self._analysis_worker.cancel()
            self.status_message.emit("已请求取消，正在安全结束当前文件读取")

    def _load_page(self) -> None:
        if self._report is None:
            return
        category_data = self.category_filter.currentData()
        category = FileCategory(category_data) if category_data else None
        try:
            rows = self._runtime.analysis_results.page_candidates(
                self._report.analysis_session_id,
                offset=self._page_offset,
                limit=self._page_size,
                category=category,
                search=self.search_input.text(),
                minimum_size_bytes=self.result_minimum_mb.value() * 1_048_576,
                sort_by=str(self.sort_field.currentData()),
                descending=bool(self.sort_direction.currentData()),
            )
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.results_table.setRowCount(len(rows))
        for row_index, record in enumerate(rows):
            metadata = record.metadata
            status = []
            if record.is_large:
                status.append("大文件")
            if record.inactive is not None:
                status.append("疑似长期未使用")
            if record.duplicate_group_id:
                status.append("内容重复")
            values = (
                metadata.name,
                str(metadata.path),
                str(metadata.size_bytes),
                metadata.category.value,
                metadata.modified_at.isoformat(),
                metadata.accessed_at.isoformat(),
                "、".join(status),
                record.inactive.confidence.value if record.inactive else "",
                record.duplicate_group_id or "",
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, str(metadata.path))
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Unchecked)
                if column == 2:
                    item.setData(Qt.ItemDataRole.UserRole, metadata.size_bytes)
                self.results_table.setItem(row_index, column, item)
        self.previous_button.setEnabled(self._page_offset > 0)
        self.next_button.setEnabled(len(rows) == self._page_size)
        self.open_folder_button.setEnabled(bool(rows))
        self.move_selected_button.setEnabled(bool(rows))
        self.rename_selected_button.setEnabled(bool(rows))

    @Slot()
    def _request_move_selected(self) -> None:
        paths = self._checked_result_paths()
        if not paths:
            self._show_error("请先勾选准备移动的分析结果。")
            return
        self.move_selected_requested.emit(paths)

    @Slot()
    def _request_rename_selected(self) -> None:
        paths = self._checked_result_paths()
        if not paths:
            self._show_error("请先勾选准备重命名的分析结果。")
            return
        self.rename_selected_requested.emit(paths)

    def _checked_result_paths(self) -> tuple[Path, ...]:
        """Return only explicitly checked result paths from the currently visible page."""
        paths: list[Path] = []
        for row in range(self.results_table.rowCount()):
            item = self.results_table.item(row, 0)
            if item is not None and item.checkState() is Qt.CheckState.Checked:
                paths.append(Path(str(item.data(Qt.ItemDataRole.UserRole))))
        return tuple(paths)

    @Slot()
    def _reset_and_load_page(self) -> None:
        self._page_offset = 0
        self._load_page()

    @Slot()
    def _previous_page(self) -> None:
        self._page_offset = max(0, self._page_offset - self._page_size)
        self._load_page()

    @Slot()
    def _next_page(self) -> None:
        self._page_offset += self._page_size
        self._load_page()

    @Slot()
    def _open_selected_folder(self) -> None:
        row = self.results_table.currentRow()
        if row < 0:
            return
        item = self.results_table.item(row, 0)
        if item is None:
            return
        try:
            self._runtime.explorer.select_file(Path(item.data(Qt.ItemDataRole.UserRole)))
        except Exception as exc:
            self._show_error(str(exc))

    @Slot()
    def _export_report(self) -> None:
        if self._plan is None or self._report is None:
            return
        selected, chosen_filter = QFileDialog.getSaveFileName(
            self,
            "导出新的分析报告（不会覆盖已有文件）",
            "file-analysis-report.csv",
            "CSV (*.csv);;JSON (*.json)",
        )
        if not selected:
            return
        format = ReportFormat.JSON if "JSON" in chosen_filter else ReportFormat.CSV
        target = Path(selected)
        if target.suffix.casefold() != f".{format.value}":
            target = target.with_suffix(f".{format.value}")
        try:
            result = self._runtime.report_exporter.export(
                target,
                format,
                self._plan,
                self._report,
            )
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.status_message.emit(f"报告已创建：{result.path}；{result.rows:,} 行；未覆盖任何文件")

    @Slot()
    def _explain_report(self) -> None:
        if (
            self._services is None
            or self._services.explainer is None
            or self._plan is None
            or self._report is None
        ):
            return
        try:
            consent = self._services.explainer.request_external_consent(
                self._plan,
                self._report.summary,
            )
            approved = (
                QMessageBox.question(
                    self,
                    "确认发送聚合统计",
                    consent.object_summary,
                )
                == QMessageBox.StandardButton.Yes
            )
            self._runtime.external_consent.resolve(consent.confirmation_id, approved)
            if not approved:
                return
            worker = ExplanationWorker(
                self._services.explainer,
                self._plan,
                self._report,
                consent.confirmation_id,
            )
            worker.signals.completed.connect(self.summary_view.setPlainText)
            worker.signals.failed.connect(self._show_error)
            self._explanation_worker = worker
            QThreadPool.globalInstance().start(worker)
        except Exception as exc:
            self._show_error(str(exc))

    def _selected_root_ids(self) -> tuple[UUID, ...]:
        return tuple(
            UUID(item.data(Qt.ItemDataRole.UserRole))
            for item in self.authorized_list.selectedItems()
        )

    @Slot()
    def _invalidate_plan(self) -> None:
        if self._building or self._plan is None:
            return
        self._plan = None
        self._confirmation = None
        self.confirm_button.setEnabled(False)
        self.reject_button.setEnabled(False)
        self.run_button.setEnabled(False)
        self.risk_label.setText("风险：条件已变化，旧确认已失效，请重新生成计划")

    def shutdown(self) -> None:
        """Request cancellation before the application waits for workers."""
        self.cancel()

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "操作未执行", message)
        self.status_message.emit(message)
