"""Non-technical Stage 4D4 Fresh Preview and double-confirmation dialog."""

from __future__ import annotations

import logging
from html import escape

from PySide6.QtCore import QThreadPool, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from pc_manager_agent.app.runtime import ApplicationRuntime, ResidualCleanupServices
from pc_manager_agent.domain.residual_cleanup import (
    CleanupEligibilityDecision,
    ResidualCleanupAssessment,
    ResidualCleanupExecutionReport,
    ResidualCleanupRequest,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.orchestration.residual_cleanup import (
    PreparedResidualCleanup,
    RuntimeResidualCleanup,
)
from pc_manager_agent.ui.residual_cleanup_workers import (
    PreparedResidualCleanupUiState,
    ResidualCleanupExecuteWorker,
    ResidualCleanupPrepareWorker,
    ResidualCleanupRuntimeWorker,
    require_cleanup_prepared,
    require_cleanup_report,
    require_cleanup_runtime,
)

_LOG = logging.getLogger(__name__)


class ResidualCleanupDialog(QDialog):
    """Keep old report intent separate from fresh R2 execution authority."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        request: ResidualCleanupRequest,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._request = request
        self._services: ResidualCleanupServices | None = None
        self._prepared: PreparedResidualCleanup | None = None
        self._runtime_state: RuntimeResidualCleanup | None = None
        self._worker: object | None = None
        self._closing = False
        self._stage = "PREPARING"
        self.setWindowTitle("安全残留清理 — Fresh Revalidation")
        self.resize(1_100, 720)
        self._build_ui()
        self._start_prepare()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.risk_label = QLabel("尚未授权：正在重新扫描所选对象。旧报告和旧确认不能执行清理。")
        self.risk_label.setStyleSheet("font-weight: 700; color: #8a5a00;")
        self.summary = QTextBrowser()
        self.summary.setMaximumHeight(210)
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            (
                "对象",
                "Fresh 状态",
                "分类",
                "归属",
                "保护",
                "文件",
                "目录",
                "大小",
                "回收能力",
                "原因",
            )
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.primary = QPushButton("正在安全重新验证")
        self.primary.setEnabled(False)
        self.cancel = QPushButton("取消（默认）")
        self.cancel.setDefault(True)
        buttons.addWidget(self.primary)
        buttons.addWidget(self.cancel)
        layout.addWidget(self.risk_label)
        layout.addWidget(self.summary)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.progress)
        layout.addLayout(buttons)
        self.primary.clicked.connect(self._primary_clicked)
        self.cancel.clicked.connect(self._cancel_clicked)

    def _start_prepare(self) -> None:
        worker = ResidualCleanupPrepareWorker(self._runtime, self._request)
        worker.signals.completed.connect(self._prepared_completed)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _prepared_completed(self, value: object) -> None:
        self._worker = None
        if self._closing:
            return
        try:
            state = require_cleanup_prepared(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._services = state.services
        self._populate_assessment(state.assessment)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        if state.prepared is None:
            self._stage = "BLOCKED"
            self.risk_label.setText("已安全阻止：本次选择包含不可清理或证据不足的对象。")
            self.risk_label.setStyleSheet("font-weight: 700; color: #9b1c1c;")
            self.summary.setHtml(
                "<h3>没有执行任何清理</h3>"
                "<p>原选择作为一个整体未通过 Fresh Revalidation。请关闭后重新选择；"
                "系统不会静默清理其中一部分。</p>"
            )
            self.primary.setText("关闭并重新选择")
            self.primary.setEnabled(True)
            return
        self._prepared = state.prepared
        self._stage = "PLAN"
        self._show_plan_confirmation(state)

    def _show_plan_confirmation(self, state: PreparedResidualCleanupUiState) -> None:
        prepared = state.prepared
        if prepared is None:
            return
        plan = prepared.plan
        risk_text = "R2 HIGH IMPACT" if plan.risk_level is RiskLevel.R2_HIGH_IMPACT else "R2"
        self.risk_label.setText(
            f"计划确认：{risk_text}；操作仅为移入 Windows 回收站；恢复能力 MANUAL。"
        )
        self.risk_label.setStyleSheet("font-weight: 700; color: #9b1c1c;")
        self.summary.setHtml(
            "<h3>第一次确认：受控清理计划</h3>"
            f"<p><b>候选：</b>{plan.total_items}；"
            f"<b>内部对象：</b>{plan.contained_object_count}；"
            f"<b>总大小：</b>{_format_size(plan.total_bytes)}；"
            f"<b>风险：</b>{risk_text}</p>"
            "<p>不会永久删除、不会清理注册表、配置、数据库、用户数据、"
            "MSIX 用户数据、共享目录或重解析点。</p>"
            "<p>确认后还会再次完整扫描；仍不会立即执行。</p>"
        )
        self.primary.setText("确认计划并再次扫描")
        self.primary.setEnabled(True)

    @Slot()
    def _primary_clicked(self) -> None:
        if self._stage == "PLAN":
            services, prepared = self._require_prepared()
            if services is None or prepared is None:
                return
            try:
                services.service.resolve_plan_confirmation(prepared, True)
            except Exception as exc:
                self._failed(f"{type(exc).__name__}: {exc}")
                return
            runtime_worker = ResidualCleanupRuntimeWorker(services, prepared)
            runtime_worker.signals.completed.connect(self._runtime_prepared)
            runtime_worker.signals.failed.connect(self._failed)
            self._worker = runtime_worker
            self._stage = "RUNTIME_SCANNING"
            self.progress.setRange(0, 0)
            self.primary.setEnabled(False)
            self.cancel.setText("取消重新扫描")
            self.summary.setHtml(
                "<h3>正在进行执行前 Fresh Revalidation</h3>"
                "<p>正在重新核对身份、目录树、保护等级和回收能力；没有修改文件。</p>"
            )
            QThreadPool.globalInstance().start(runtime_worker)
        elif self._stage == "RUNTIME":
            services, _prepared = self._require_prepared()
            runtime = self._runtime_state
            if services is None or runtime is None:
                self._failed("内部即时确认状态不完整，未执行清理。")
                return
            try:
                services.service.resolve_runtime_confirmation(runtime, True)
            except Exception as exc:
                self._failed(f"{type(exc).__name__}: {exc}")
                return
            execute_worker = ResidualCleanupExecuteWorker(services, runtime)
            execute_worker.signals.completed.connect(self._execution_completed)
            execute_worker.signals.failed.connect(self._failed)
            self._worker = execute_worker
            self._stage = "EXECUTING"
            self.progress.setRange(0, 0)
            self.primary.setEnabled(False)
            self.cancel.setText("停止后续操作")
            self.summary.setHtml(
                "<h3>正在逐项移入 Windows 回收站</h3>"
                "<p>取消只停止未来项目；已经完成的项目不会被假装撤销。</p>"
            )
            QThreadPool.globalInstance().start(execute_worker)
        elif self._stage in {"DONE", "BLOCKED", "FAILED"}:
            self.accept()

    @Slot(object)
    def _runtime_prepared(self, value: object) -> None:
        self._worker = None
        if self._closing:
            return
        try:
            runtime = require_cleanup_runtime(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._runtime_state = runtime
        self._stage = "RUNTIME"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        plan = runtime.prepared.plan
        risk = "R2 HIGH IMPACT" if plan.risk_level is RiskLevel.R2_HIGH_IMPACT else "R2"
        self.summary.setHtml(
            "<h3>第二次确认：立即移入 Windows 回收站</h3>"
            f"<p>将处理 <b>{plan.total_items}</b> 项、"
            f"共 <b>{_format_size(plan.total_bytes)}</b>，风险 <b>{risk}</b>。</p>"
            "<p>恢复等级为 <b>MANUAL</b>：需要从 Windows 回收站手动还原。"
            "没有永久删除后备方式。</p>"
        )
        self.primary.setText("移入 Windows 回收站")
        self.primary.setEnabled(True)
        self.cancel.setText("取消（默认）")
        self.cancel.setDefault(True)

    @Slot(object)
    def _execution_completed(self, value: object) -> None:
        self._worker = None
        if self._closing:
            return
        try:
            report = require_cleanup_report(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._stage = "DONE"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.risk_label.setText(f"执行结束：{report.final_state.value}")
        self.summary.setHtml(_completion_html(report))
        self.primary.setText("关闭")
        self.primary.setEnabled(True)
        self.cancel.setEnabled(False)

    def _populate_assessment(self, assessment: ResidualCleanupAssessment) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(assessment.items))
        for row, candidate in enumerate(assessment.items):
            material = candidate.material
            values = (
                str(candidate.path),
                candidate.eligibility.value,
                candidate.classification.value,
                candidate.ownership_confidence.value,
                candidate.protection_level.value,
                str(material.file_count) if material is not None else "—",
                str(material.directory_count) if material is not None else "—",
                _format_size(material.tree.total_size_bytes) if material is not None else "—",
                (
                    "支持（MANUAL）"
                    if candidate.recoverability is not None
                    and candidate.recoverability.capability.available
                    else "不支持"
                ),
                "、".join(candidate.eligibility_reason_codes),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if candidate.eligibility is not CleanupEligibilityDecision.ELIGIBLE:
                    item.setToolTip("该对象不能进入本次受控清理")
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()

    @Slot(str)
    def _failed(self, message: str) -> None:
        self._worker = None
        if self._closing:
            return
        self._stage = "FAILED"
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.risk_label.setText("已安全停止")
        self.summary.setHtml(
            f"<h3>未继续执行</h3><p>{escape(message)}</p>"
            "<p>系统没有使用永久删除后备方式。请重新运行 Fresh Revalidation。</p>"
        )
        self.primary.setText("关闭")
        self.primary.setEnabled(True)

    @Slot()
    def _cancel_clicked(self) -> None:
        worker = self._worker
        cancel = getattr(worker, "cancel", None)
        if callable(cancel):
            cancel()
        services = self._services
        prepared = self._prepared
        runtime = self._runtime_state
        try:
            if self._stage == "PLAN" and services is not None and prepared is not None:
                services.service.resolve_plan_confirmation(prepared, False)
            elif self._stage == "RUNTIME" and services is not None and runtime is not None:
                services.service.resolve_runtime_confirmation(runtime, False)
        except Exception as exc:
            _LOG.warning("Could not persist cleanup rejection: %s", type(exc).__name__)
        if self._stage == "EXECUTING":
            self.cancel.setEnabled(False)
            self.summary.setHtml(
                "<h3>已请求停止后续操作</h3>"
                "<p>当前 Windows 回收站调用不会被强制终止；后续项目将跳过。</p>"
            )
            return
        self.reject()

    def _require_prepared(
        self,
    ) -> tuple[ResidualCleanupServices | None, PreparedResidualCleanup | None]:
        if self._services is None or self._prepared is None:
            self._failed("内部计划状态不完整，未执行清理。")
            return None, None
        return self._services, self._prepared

    def closeEvent(self, event: QCloseEvent) -> None:
        """Request cooperative cancellation before the dialog is destroyed."""
        self._closing = True
        self._cancel_clicked()
        event.accept()


def _format_size(value: int) -> str:
    """Return a compact binary size for non-technical confirmation text."""
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _completion_html(report: ResidualCleanupExecutionReport) -> str:
    """Build truthful completion text including partial and skipped counts."""
    return (
        "<h3>残留清理结果</h3>"
        f"<p><b>成功验证：</b>{report.completed_count}；"
        f"<b>失败：</b>{report.failed_count}；"
        f"<b>未执行：</b>{report.skipped_count}。</p>"
        f"<p><b>候选空间：</b>{_format_size(report.total_size_bytes)}；"
        "<b>恢复：</b>MANUAL，需要从 Windows 回收站手动还原。</p>"
        "<p>没有永久删除任何内容。</p>"
    )
