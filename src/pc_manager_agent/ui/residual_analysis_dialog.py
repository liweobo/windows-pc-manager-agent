"""Non-technical report-only UI for Stage 4D3 possible software residuals."""

from __future__ import annotations

import logging
from html import escape
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import Qt, QThreadPool, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from pc_manager_agent.app.runtime import ApplicationRuntime, ResidualAnalysisServices
from pc_manager_agent.confirmation.models import ConfirmationRequest
from pc_manager_agent.domain.plans import TaskPlan
from pc_manager_agent.domain.residual_cleanup import ResidualCleanupRequest
from pc_manager_agent.domain.software_residuals import (
    ResidualCandidate,
    ResidualClassification,
    ResidualReport,
    UninstallContext,
    UserDataProtectionLevel,
)
from pc_manager_agent.reporting.residual_exporter import (
    ResidualReportFormat,
)
from pc_manager_agent.ui.residual_analysis_workers import (
    ResidualAnalyzeWorker,
    ResidualExportWorker,
    ResidualPrepareWorker,
    require_prepared_residual,
    require_residual_export,
    require_residual_report,
)
from pc_manager_agent.ui.residual_cleanup_dialog import ResidualCleanupDialog

_LOG = logging.getLogger(__name__)


class ResidualAnalysisDialog(QDialog):
    """Confirm one R0 plan and display protected metadata without cleanup controls."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        transaction_id: UUID,
        *,
        user_goal: str = "分析本次卸载后可能存在的残留，只生成报告",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._transaction_id = transaction_id
        self._user_goal = user_goal
        self._services: ResidualAnalysisServices | None = None
        self._plan: TaskPlan | None = None
        self._context: UninstallContext | None = None
        self._confirmation: ConfirmationRequest | None = None
        self._report: ResidualReport | None = None
        self._worker: object | None = None
        self._closing = False
        self._stage = "PREPARING"
        self.setWindowTitle("卸载后可能残留 — 只读分析")
        self.resize(1_080, 760)
        self._build_ui()
        self._start_prepare()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.risk_label = QLabel(
            "R0 只读：仅查看精确已知路径的元数据；不读取内容，不删除、不移动、不进回收站。"
        )
        self.risk_label.setStyleSheet("font-weight: 700; color: #1e5c3a;")
        self.summary = QTextBrowser()
        self.summary.setMaximumHeight(180)
        self.summary.setOpenExternalLinks(False)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("按路径搜索")
        self.classification_filter = QComboBox()
        self.classification_filter.addItem("全部分类", None)
        for classification in ResidualClassification:
            self.classification_filter.addItem(classification.value, classification.value)
        self.protection_filter = QComboBox()
        self.protection_filter.addItem("全部保护等级", None)
        for protection in UserDataProtectionLevel:
            self.protection_filter.addItem(protection.value, protection.value)
        filters.addWidget(self.search, 2)
        filters.addWidget(self.classification_filter, 1)
        filters.addWidget(self.protection_filter, 1)
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            (
                "申请安全复核",
                "路径",
                "对象",
                "大小",
                "分类",
                "归属可信度",
                "保护等级",
                "证据摘要",
                "修改时间",
                "建议",
            )
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(self.details)
        splitter.setSizes((430, 180))
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        action_buttons = QHBoxLayout()
        self.export_json = QPushButton("导出 JSON")
        self.export_csv = QPushButton("导出 CSV")
        self.open_location = QPushButton("打开所在位置")
        self.prepare_cleanup = QPushButton("重新验证所选项")
        for button in (
            self.export_json,
            self.export_csv,
            self.open_location,
            self.prepare_cleanup,
        ):
            button.setEnabled(False)
            action_buttons.addWidget(button)
        action_buttons.addStretch(1)
        self.primary = QPushButton("正在准备只读计划")
        self.cancel = QPushButton("取消（默认）")
        self.primary.setEnabled(False)
        self.cancel.setDefault(True)
        action_buttons.addWidget(self.primary)
        action_buttons.addWidget(self.cancel)
        layout.addWidget(self.risk_label)
        layout.addWidget(self.summary)
        layout.addLayout(filters)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.progress)
        layout.addLayout(action_buttons)
        self.primary.clicked.connect(self._primary_clicked)
        self.cancel.clicked.connect(self._cancel_clicked)
        self.search.textChanged.connect(lambda _text: self._apply_filters())
        self.classification_filter.currentIndexChanged.connect(lambda _index: self._apply_filters())
        self.protection_filter.currentIndexChanged.connect(lambda _index: self._apply_filters())
        self.table.itemSelectionChanged.connect(self._show_selected_details)
        self.export_json.clicked.connect(lambda: self._export(ResidualReportFormat.JSON))
        self.export_csv.clicked.connect(lambda: self._export(ResidualReportFormat.CSV))
        self.open_location.clicked.connect(self._open_selected_location)
        self.prepare_cleanup.clicked.connect(self._open_cleanup)
        self.table.itemChanged.connect(lambda _item: self._update_cleanup_button())

    def _start_prepare(self) -> None:
        worker = ResidualPrepareWorker(self._runtime, self._user_goal, self._transaction_id)
        worker.signals.completed.connect(self._prepared)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _prepared(self, value: object) -> None:
        self._worker = None
        if self._closing:
            return
        try:
            prepared = require_prepared_residual(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        if not prepared.review.approved:
            self._failed("安全审查未通过，未读取任何残留路径。")
            return
        try:
            confirmation = prepared.services.service.request_plan_confirmation(
                prepared.plan, prepared.context
            )
        except Exception as exc:
            self._failed(f"{type(exc).__name__}: {exc}")
            return
        self._services = prepared.services
        self._plan = prepared.plan
        self._context = prepared.context
        self._confirmation = confirmation
        self._stage = "PLAN"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        paths = "".join(
            f"<li>{escape(str(path))}</li>" for path in prepared.plan.scope.included_paths
        )
        confidence_note = (
            "卸载已验证。"
            if prepared.context.verified_removed
            else "卸载未完全验证，报告可信度会降低。"
        )
        self.summary.setHtml(
            "<h3>计划确认：尚未开始扫描</h3>"
            f"<p><b>软件：</b>{escape(prepared.context.display_name)}；"
            f"<b>方式：</b>{prepared.context.mechanism.value}；{confidence_note}</p>"
            f"<p><b>精确范围：</b></p><ul>{paths}</ul>"
            "<p><b>预计影响：</b>修改 0，删除 0；回滚等级 NONE（没有需要撤销的数据变更）。</p>"
        )
        self.primary.setText("确认只读计划并开始分析")
        self.primary.setEnabled(True)

    @Slot()
    def _primary_clicked(self) -> None:
        if self._stage == "PLAN":
            state = self._required_state()
            if state is None:
                return
            services, plan, context, confirmation = state
            try:
                services.service.resolve_plan_confirmation(
                    confirmation.confirmation_id, True, plan, context
                )
            except Exception as exc:
                self._failed(f"{type(exc).__name__}: {exc}")
                return
            worker = ResidualAnalyzeWorker(services, plan, context)
            worker.signals.completed.connect(self._completed)
            worker.signals.failed.connect(self._failed)
            self._worker = worker
            self._stage = "ANALYZING"
            self.progress.setRange(0, 0)
            self.primary.setEnabled(False)
            self.summary.setHtml(
                "<h3>正在读取文件系统元数据</h3>"
                "<p>可随时取消；不会打开文件内容，也不会修改任何对象。</p>"
            )
            QThreadPool.globalInstance().start(worker)
        elif self._stage in {"DONE", "FAILED"}:
            self.accept()

    @Slot(object)
    def _completed(self, value: object) -> None:
        self._worker = None
        if self._closing:
            return
        try:
            report = require_residual_report(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._report = report
        self._stage = "DONE"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.summary.setHtml(
            "<h3>只读分析完成</h3>"
            f"<p><b>状态：</b>{report.status.value}；"
            f"<b>候选：</b>{report.summary.candidates}；"
            f"<b>大小：</b>{_format_size(report.summary.total_size_bytes)}；"
            f"<b>受保护大小：</b>{_format_size(report.summary.protected_size_bytes)}；"
            f"<b>问题：</b>{report.summary.issues}</p>"
            "<p><b>删除执行：</b>否。候选归属可信度不代表清理安全性。</p>"
        )
        self._populate_table(report.candidates)
        self.export_json.setEnabled(True)
        self.export_csv.setEnabled(True)
        self.primary.setText("关闭")
        self.primary.setEnabled(True)

    def _populate_table(self, candidates: tuple[ResidualCandidate, ...]) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(candidates))
        ordered = sorted(
            candidates,
            key=lambda item: (
                item.protection_level is not UserDataProtectionLevel.STRONGLY_PROTECTED,
                str(item.path).casefold(),
            ),
        )
        for row, candidate in enumerate(ordered):
            values = (
                "复核",
                str(candidate.path),
                candidate.object_type.value,
                _format_size(candidate.size_bytes),
                candidate.classification.value,
                candidate.ownership_confidence.value,
                candidate.protection_level.value,
                "、".join(item.code for item in candidate.ownership_evidence),
                candidate.modified_at.isoformat() if candidate.modified_at is not None else "—",
                candidate.recommendation.value,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, str(candidate.candidate_id))
                if column == 0:
                    item.setFlags(
                        item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                    )
                    item.setCheckState(Qt.CheckState.Unchecked)
                    if not _potential_cleanup_intent(candidate):
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                        item.setToolTip("当前报告已显示受保护或证据不足；不能申请通用残留清理。")
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self._apply_filters()
        self._update_cleanup_button()

    @Slot()
    def _apply_filters(self) -> None:
        search = self.search.text().strip().casefold()
        classification = self.classification_filter.currentData()
        protection = self.protection_filter.currentData()
        for row in range(self.table.rowCount()):
            path_item = self.table.item(row, 1)
            classification_item = self.table.item(row, 4)
            protection_item = self.table.item(row, 6)
            if path_item is None or classification_item is None or protection_item is None:
                self.table.setRowHidden(row, True)
                continue
            path = path_item.text().casefold()
            row_classification = classification_item.text()
            row_protection = protection_item.text()
            visible = (
                (not search or search in path)
                and (classification is None or classification == row_classification)
                and (protection is None or protection == row_protection)
            )
            self.table.setRowHidden(row, not visible)

    @Slot()
    def _show_selected_details(self) -> None:
        candidate = self._selected_candidate()
        self.open_location.setEnabled(candidate is not None)
        if candidate is None:
            self.details.clear()
            return
        evidence = "".join(
            f"<li>{escape(item.code)}：{escape(item.explanation)}</li>"
            for item in candidate.ownership_evidence
        )
        flags = "、".join(candidate.risk_flags) or "无"
        modified_text = candidate.modified_at.isoformat() if candidate.modified_at else "未知"
        self.details.setHtml(
            f"<h3>{escape(candidate.path.name)}</h3>"
            f"<p><b>完整路径：</b>{escape(str(candidate.path))}</p>"
            f"<p><b>分类：</b>{candidate.classification.value}；"
            f"<b>保护：</b>{candidate.protection_level.value}；"
            f"<b>建议：</b>{candidate.recommendation.value}</p>"
            f"<p><b>对象身份摘要：</b>{candidate.identity.canonical_digest()}；"
            f"<b>最后修改：</b>{escape(modified_text)}；"
            f"<b>扫描来源：</b>{candidate.source.value}</p>"
            f"<p><b>分类理由：</b>{escape('、'.join(candidate.classification_reasons) or '无')}；"
            f"<b>保护理由：</b>{escape('、'.join(candidate.protection_reasons) or '无')}</p>"
            f"<p><b>风险标记：</b>{escape(flags)}</p>"
            f"<p><b>归属证据：</b></p><ul>{evidence}</ul>"
            "<p>此页面没有清理授权；未来任何处理都必须重新扫描、重新计划和重新确认。</p>"
        )

    def _selected_candidate(self) -> ResidualCandidate | None:
        report = self._report
        row = self.table.currentRow()
        if report is None or row < 0:
            return None
        item = self.table.item(row, 1)
        if item is None:
            return None
        candidate_id = item.data(Qt.ItemDataRole.UserRole)
        return next(
            (
                candidate
                for candidate in report.candidates
                if str(candidate.candidate_id) == candidate_id
            ),
            None,
        )

    def _export(self, format: ResidualReportFormat) -> None:
        report = self._report
        services = self._services
        plan = self._plan
        context = self._context
        if report is None or services is None or plan is None or context is None:
            return
        suffix = f"*.{format.value}"
        selected, _filter = QFileDialog.getSaveFileName(
            self, "导出只读残留报告", f"residual-report.{format.value}", suffix
        )
        if not selected:
            return
        worker = ResidualExportWorker(
            services,
            plan,
            context,
            report,
            Path(selected),
            format,
        )
        worker.signals.completed.connect(self._export_completed)
        worker.signals.failed.connect(self._export_failed)
        self._worker = worker
        self.export_json.setEnabled(False)
        self.export_csv.setEnabled(False)
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _export_completed(self, value: object) -> None:
        self._worker = None
        if self._closing:
            return
        try:
            require_residual_export(value)
        except TypeError as exc:
            self._export_failed(str(exc))
            return
        self.export_json.setEnabled(True)
        self.export_csv.setEnabled(True)
        QMessageBox.information(self, "导出完成", "报告已创建；现有文件从未被覆盖。")

    @Slot(str)
    def _export_failed(self, message: str) -> None:
        self._worker = None
        if self._closing:
            return
        self.export_json.setEnabled(self._report is not None)
        self.export_csv.setEnabled(self._report is not None)
        QMessageBox.warning(self, "导出失败", message)

    @Slot()
    def _open_selected_location(self) -> None:
        candidate = self._selected_candidate()
        services = self._services
        if candidate is None or services is None:
            return
        try:
            services.explorer.select_candidate(candidate)
        except Exception as exc:
            QMessageBox.warning(self, "无法打开位置", f"{type(exc).__name__}: {exc}")

    @Slot()
    def _update_cleanup_button(self) -> None:
        """Enable Fresh Revalidation only when the user explicitly checked rows."""
        self.prepare_cleanup.setEnabled(bool(self._selected_cleanup_ids()))

    def _selected_cleanup_ids(self) -> tuple[UUID, ...]:
        """Return checked report IDs as intent only; paths never leave the local resolver."""
        selected: list[UUID] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None or item.checkState() is not Qt.CheckState.Checked:
                continue
            selected.append(UUID(str(item.data(Qt.ItemDataRole.UserRole))))
        return tuple(selected)

    @Slot()
    def _open_cleanup(self) -> None:
        """Open an independent Fresh Revalidation and double-confirmation workflow."""
        report = self._report
        selected = self._selected_cleanup_ids()
        if report is None or not selected:
            return
        dialog = ResidualCleanupDialog(
            self._runtime,
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=selected,
            ),
            self,
        )
        dialog.exec()

    @Slot(str)
    def _failed(self, message: str) -> None:
        self._worker = None
        if self._closing:
            return
        self._stage = "FAILED"
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.summary.setHtml(
            f"<h3>已安全停止</h3><p>{escape(message)}</p>"
            "<p>没有删除、移动、回收或修改任何扫描对象。</p>"
        )
        self.primary.setText("关闭")
        self.primary.setEnabled(True)

    @Slot()
    def _cancel_clicked(self) -> None:
        worker = self._worker
        cancel = getattr(worker, "cancel", None)
        if callable(cancel):
            cancel()
        if self._stage == "PLAN":
            state = self._required_state()
            if state is not None:
                services, plan, context, confirmation = state
                try:
                    services.service.resolve_plan_confirmation(
                        confirmation.confirmation_id, False, plan, context
                    )
                except Exception as exc:
                    _LOG.warning(
                        "Could not record rejected residual plan confirmation: %s",
                        type(exc).__name__,
                    )
        self.reject()

    def _required_state(
        self,
    ) -> (
        tuple[
            ResidualAnalysisServices,
            TaskPlan,
            UninstallContext,
            ConfirmationRequest,
        ]
        | None
    ):
        services = self._services
        plan = self._plan
        context = self._context
        confirmation = self._confirmation
        if services is None or plan is None or context is None or confirmation is None:
            self._failed("内部状态不完整，未开始扫描。")
            return None
        return services, plan, context, confirmation

    def closeEvent(self, event: QCloseEvent) -> None:
        """Request cooperative cancellation before closing the dialog."""
        self._closing = True
        self._cancel_clicked()
        event.accept()


def _format_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _potential_cleanup_intent(candidate: ResidualCandidate) -> bool:
    """Filter obvious blocks without claiming the stale report proves eligibility."""
    return (
        candidate.classification
        in {
            ResidualClassification.PROGRAM_RESIDUAL,
            ResidualClassification.CACHE,
            ResidualClassification.LOG,
            ResidualClassification.SHORTCUT,
        }
        and candidate.ownership_confidence.value == "high"
        and not candidate.reparse_or_symlink
        and (
            candidate.protection_level
            in {UserDataProtectionLevel.NONE, UserDataProtectionLevel.CAUTION}
            or candidate.classification is ResidualClassification.SHORTCUT
        )
    )
