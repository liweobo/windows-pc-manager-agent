"""Non-technical Stage 4E2 Fresh selection and double-confirmation dialogs."""

from __future__ import annotations

import logging
from html import escape
from uuid import UUID

from PySide6.QtCore import Qt, QThreadPool, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
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

from pc_manager_agent.app.runtime import ApplicationRuntime, SystemCleanupServices
from pc_manager_agent.domain.optimization_receipts import OptimizationReceiptKind
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.system_cleanup_execution import (
    CleanupEligibilityDecision,
    CleanupResult,
    CleanupVerificationStatus,
    SystemCleanupAssessment,
    SystemCleanupRequest,
)
from pc_manager_agent.orchestration.system_cleanup import (
    PreparedRecycleBinEmpty,
    PreparedSystemCleanup,
    RuntimeRecycleBinEmpty,
    RuntimeSystemCleanup,
)
from pc_manager_agent.ui.domain_review_events import ObservedDomainDialog
from pc_manager_agent.ui.system_cleanup_workers import (
    RecycleBinEmptyExecuteWorker,
    RecycleBinEmptyPrepareWorker,
    RecycleBinEmptyRuntimeWorker,
    SystemCleanupAssessmentWorker,
    SystemCleanupExecuteWorker,
    SystemCleanupRuntimeWorker,
    require_assessment,
    require_cleanup_result,
    require_empty_result,
    require_prepared_empty,
    require_runtime_cleanup,
    require_runtime_empty,
)

_LOG = logging.getLogger(__name__)


class SystemCleanupDialog(ObservedDomainDialog):
    """Require Fresh discovery, a second exact selection, and two approvals."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        request: SystemCleanupRequest,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._request = request
        self._services: SystemCleanupServices | None = None
        self._assessment: SystemCleanupAssessment | None = None
        self._prepared: PreparedSystemCleanup | None = None
        self._runtime_state: RuntimeSystemCleanup | None = None
        self._worker: object | None = None
        self._stage = "ASSESSING"
        self._closing = False
        self.setWindowTitle("受控系统清理 — Fresh 安全评估")
        self.resize(1_160, 740)
        self._build_ui()
        self._start_assessment()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.risk_label = QLabel(
            "尚未授权：旧分析只表达意向，正在重新检查身份、内容类型、近期活动和回收能力。"
        )
        self.risk_label.setWordWrap(True)
        self.risk_label.setStyleSheet("font-weight: 700; color: #8a5a00;")
        self.summary = QTextBrowser()
        self.summary.setMaximumHeight(210)
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            (
                "选择",
                "对象",
                "Fresh 状态",
                "类别",
                "保护",
                "文件",
                "目录",
                "大小",
                "恢复",
                "原因",
            )
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.itemChanged.connect(self._selection_changed)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.primary = QPushButton("正在 Fresh 安全评估")
        self.primary.setEnabled(False)
        self.cancel = QPushButton("取消（默认）")
        self.cancel.setDefault(True)
        actions.addWidget(self.primary)
        actions.addWidget(self.cancel)
        layout.addWidget(self.risk_label)
        layout.addWidget(self.summary)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.progress)
        layout.addLayout(actions)
        self.primary.clicked.connect(self._primary_clicked)
        self.cancel.clicked.connect(self._cancel_clicked)

    def _start_assessment(self) -> None:
        worker = SystemCleanupAssessmentWorker(self._runtime, self._request)
        worker.signals.completed.connect(self._assessment_completed)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _assessment_completed(self, value: object) -> None:
        self._worker = None
        if self._closing:
            return
        try:
            state = require_assessment(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._services = state.services
        self._assessment = state.assessment
        self._populate_assessment(state.assessment)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self._stage = "SELECTION"
        self.risk_label.setText(
            "Fresh 安全评估完成：仅 ELIGIBLE 行可勾选，默认全部不选；没有“全选”。"
        )
        self.summary.setHtml(
            "<h3>第二次、逐对象选择</h3>"
            f"<p><b>可选：</b>{state.assessment.eligible_count}；"
            f"<b>已阻止：</b>{state.assessment.blocked_count}；"
            f"<b>需人工复查：</b>{state.assessment.manual_review_count}；"
            f"<b>转交其他安全流程：</b>{state.assessment.deferred_count}。</p>"
            "<p>勾选后还会显示确切影响并要求两次确认。普通清理只移入 Windows 回收站，"
            "恢复为 MANUAL；移入回收站不等于磁盘空间已经释放。</p>"
        )
        self.primary.setText("生成所选对象的受控计划")
        self.primary.setEnabled(False)
        if state.assessment.eligible_count == 0:
            self._stage = "BLOCKED"
            self.primary.setText("关闭")
            self.primary.setEnabled(True)

    def _populate_assessment(self, assessment: SystemCleanupAssessment) -> None:
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(assessment.items))
        for row, candidate in enumerate(assessment.items):
            selectable = candidate.eligibility is CleanupEligibilityDecision.ELIGIBLE
            selector = QTableWidgetItem()
            flags = Qt.ItemFlag.ItemIsEnabled
            if selectable:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            selector.setFlags(flags)
            selector.setCheckState(Qt.CheckState.Unchecked)
            selector.setData(Qt.ItemDataRole.UserRole, str(candidate.item_ref))
            self.table.setItem(row, 0, selector)
            material = candidate.material
            values = (
                str(candidate.path) if candidate.path is not None else "不提供直接对象路径",
                candidate.eligibility.value,
                candidate.category.value,
                candidate.protection_level.value,
                str(material.file_count) if material is not None else "—",
                str(material.directory_count) if material is not None else "—",
                _format_size(material.tree.total_size_bytes) if material is not None else "—",
                "MANUAL" if selectable else "不可执行",
                "、".join(candidate.reason_codes),
            )
            for column, text in enumerate(values, start=1):
                item = QTableWidgetItem(text)
                if not selectable:
                    item.setToolTip("此对象不能进入 Stage 4E2 直接清理")
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.table.blockSignals(False)

    @Slot(QTableWidgetItem)
    def _selection_changed(self, item: QTableWidgetItem) -> None:
        if self._stage == "SELECTION" and item.column() == 0:
            self.primary.setEnabled(bool(self._selected_item_refs()))

    def _selected_item_refs(self) -> tuple[UUID, ...]:
        selected: list[UUID] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() is Qt.CheckState.Checked:
                selected.append(UUID(str(item.data(Qt.ItemDataRole.UserRole))))
        return tuple(selected)

    @Slot()
    def _primary_clicked(self) -> None:
        if self._stage == "SELECTION":
            services = self._services
            assessment = self._assessment
            selected = self._selected_item_refs()
            if services is None or assessment is None or not selected:
                return
            try:
                prepared = services.service.prepare(assessment, selected)
            except Exception as exc:
                self._failed(f"{type(exc).__name__}: {exc}")
                return
            self._prepared = prepared
            self.publish_domain_preview(
                OptimizationReceiptKind.CLEANUP, prepared.plan.transaction_id
            )
            self._stage = "PLAN"
            risk = _risk_text(prepared.plan.risk_level)
            self.risk_label.setText(f"第一次确认：{risk}；仅移入 Windows 回收站；恢复能力 MANUAL。")
            self.risk_label.setStyleSheet("font-weight: 700; color: #9b1c1c;")
            self.summary.setHtml(
                "<h3>计划确认</h3>"
                f"<p>将处理 <b>{prepared.plan.total_items}</b> 项，内部对象 "
                f"<b>{prepared.plan.contained_object_count}</b> 个，总大小 "
                f"<b>{_format_size(prepared.plan.total_observed_bytes)}</b>，风险 "
                f"<b>{risk}</b>。</p>"
                "<p><b>恢复等级 MANUAL：</b>成功项目需要从 Windows 回收站手动还原；"
                "移入回收站并不等于释放磁盘空间。</p>"
                "<p>不会永久删除、不会请求管理员权限、不会停止进程或服务。"
                "确认后还会重新扫描，并不会立即移动。</p>"
            )
            self.primary.setText("确认计划并再次 Fresh 检查")
            self.primary.setEnabled(True)
        elif self._stage == "PLAN":
            services, plan_state = self._require_item_plan()
            if services is None or plan_state is None:
                return
            try:
                services.service.resolve_plan_confirmation(plan_state, True)
            except Exception as exc:
                self._failed(f"{type(exc).__name__}: {exc}")
                return
            runtime_worker = SystemCleanupRuntimeWorker(services, plan_state)
            runtime_worker.signals.completed.connect(self._runtime_completed)
            runtime_worker.signals.failed.connect(self._failed)
            self._worker = runtime_worker
            self._stage = "RUNTIME_SCANNING"
            self.progress.setRange(0, 0)
            self.primary.setEnabled(False)
            QThreadPool.globalInstance().start(runtime_worker)
        elif self._stage == "RUNTIME":
            services, _prepared = self._require_item_plan()
            runtime = self._runtime_state
            if services is None or runtime is None:
                self._failed("即时确认状态不完整，未执行清理。")
                return
            try:
                services.service.resolve_runtime_confirmation(runtime, True)
            except Exception as exc:
                self._failed(f"{type(exc).__name__}: {exc}")
                return
            execute_worker = SystemCleanupExecuteWorker(services, runtime)
            execute_worker.signals.completed.connect(self._execution_completed)
            execute_worker.signals.failed.connect(self._failed)
            self._worker = execute_worker
            self._stage = "EXECUTING"
            self.progress.setRange(0, 0)
            self.primary.setEnabled(False)
            self.cancel.setText("停止后续项目")
            QThreadPool.globalInstance().start(execute_worker)
        elif self._stage in {"DONE", "BLOCKED", "FAILED"}:
            self.accept()

    @Slot(object)
    def _runtime_completed(self, value: object) -> None:
        self._worker = None
        if self._closing:
            return
        try:
            runtime = require_runtime_cleanup(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._runtime_state = runtime
        self._stage = "RUNTIME"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        plan = runtime.prepared.plan
        self.summary.setHtml(
            "<h3>第二次确认：立即移入 Windows 回收站</h3>"
            f"<p>对象 <b>{plan.total_items}</b> 项、内部对象 "
            f"<b>{plan.contained_object_count}</b> 个、总大小 "
            f"<b>{_format_size(plan.total_observed_bytes)}</b>、风险 "
            f"<b>{_risk_text(plan.risk_level)}</b>。</p>"
            "<p>恢复等级 <b>MANUAL</b>：需要在 Windows 回收站中手动还原。"
            "没有永久删除后备方案。</p>"
        )
        self.primary.setText("立即移入 Windows 回收站")
        self.primary.setEnabled(True)
        self.cancel.setText("取消（默认）")

    @Slot(object)
    def _execution_completed(self, value: object) -> None:
        self._worker = None
        if self._closing:
            return
        try:
            result = require_cleanup_result(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._stage = "DONE"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.risk_label.setText(f"执行结束：{result.final_state.value}")
        self.summary.setHtml(_cleanup_completion_html(result))
        self.primary.setText("关闭")
        self.primary.setEnabled(True)
        self.cancel.setEnabled(False)

    def _require_item_plan(
        self,
    ) -> tuple[SystemCleanupServices | None, PreparedSystemCleanup | None]:
        if self._services is None or self._prepared is None:
            self._failed("受控计划状态不完整，未执行清理。")
            return None, None
        return self._services, self._prepared

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
            f"<h3>没有继续执行</h3><p>{escape(message)}</p>"
            "<p>没有使用永久删除后备方式。请重新运行只读分析和 Fresh 评估。</p>"
        )
        self.primary.setText("关闭")
        self.primary.setEnabled(True)

    @Slot()
    def _cancel_clicked(self) -> None:
        cancel = getattr(self._worker, "cancel", None)
        if callable(cancel):
            cancel()
        services = self._services
        try:
            if self._stage == "PLAN" and services is not None and self._prepared is not None:
                services.service.resolve_plan_confirmation(self._prepared, False)
            elif (
                self._stage == "RUNTIME"
                and services is not None
                and self._runtime_state is not None
            ):
                services.service.resolve_runtime_confirmation(self._runtime_state, False)
        except Exception as exc:
            _LOG.warning("Could not persist cleanup rejection: %s", type(exc).__name__)
        if self._stage == "EXECUTING":
            self.cancel.setEnabled(False)
            self.summary.setHtml(
                "<h3>已请求停止后续项目</h3>"
                "<p>当前 Windows 回收站调用不会被强制终止；后续对象将跳过。</p>"
            )
            return
        self.reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Request cooperative cancellation before closing."""
        self._closing = True
        self._cancel_clicked()
        event.accept()


class RecycleBinEmptyDialog(ObservedDomainDialog):
    """Keep exact-volume irreversible emptying separate from ordinary cleanup."""

    def __init__(self, runtime: ApplicationRuntime, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._services: SystemCleanupServices | None = None
        self._prepared: PreparedRecycleBinEmpty | None = None
        self._runtime_state: RuntimeRecycleBinEmpty | None = None
        self._worker: object | None = None
        self._stage = "PREPARING"
        self._closing = False
        self.setWindowTitle("清空 Windows 回收站 — 不可自动恢复")
        self.resize(760, 460)
        self._build_ui()
        self._start_prepare()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.risk_label = QLabel("正在独立检查当前用户、系统盘的回收站。尚未授权清空。")
        self.risk_label.setWordWrap(True)
        self.risk_label.setStyleSheet("font-weight: 700; color: #9b1c1c;")
        self.summary = QTextBrowser()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.primary = QPushButton("正在检查")
        self.primary.setEnabled(False)
        self.cancel = QPushButton("取消（默认）")
        self.cancel.setDefault(True)
        actions.addWidget(self.primary)
        actions.addWidget(self.cancel)
        layout.addWidget(self.risk_label)
        layout.addWidget(self.summary, 1)
        layout.addWidget(self.progress)
        layout.addLayout(actions)
        self.primary.clicked.connect(self._primary_clicked)
        self.cancel.clicked.connect(self._cancel_clicked)

    def _start_prepare(self) -> None:
        worker = RecycleBinEmptyPrepareWorker(self._runtime)
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
            state = require_prepared_empty(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._services = state.services
        self._prepared = state.prepared
        self.publish_domain_preview(
            OptimizationReceiptKind.RECYCLE_BIN, state.prepared.plan.transaction_id
        )
        self._stage = "PLAN"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        snapshot = state.prepared.plan.snapshot
        self.summary.setHtml(
            "<h3>第一次确认：独立不可逆计划</h3>"
            f"<p>范围：<b>{escape(str(snapshot.volume_root))}</b> 当前用户回收站；"
            f"对象 <b>{snapshot.item_count}</b> 项；已观察大小 "
            f"<b>{_format_size(snapshot.observed_size_bytes)}</b>。</p>"
            "<p>风险 <b>R2 HIGH IMPACT</b>；恢复等级 <b>NONE</b>。"
            "清空后 Agent 无法还原这些对象。</p>"
        )
        self.primary.setText("确认计划并重新检查")
        self.primary.setEnabled(True)

    @Slot()
    def _primary_clicked(self) -> None:
        if self._stage == "PLAN":
            services, prepared = self._require_plan()
            if services is None or prepared is None:
                return
            try:
                services.service.resolve_empty_plan_confirmation(prepared, True)
            except Exception as exc:
                self._failed(f"{type(exc).__name__}: {exc}")
                return
            runtime_worker = RecycleBinEmptyRuntimeWorker(services, prepared)
            runtime_worker.signals.completed.connect(self._runtime_completed)
            runtime_worker.signals.failed.connect(self._failed)
            self._worker = runtime_worker
            self._stage = "RUNTIME_SCANNING"
            self.progress.setRange(0, 0)
            self.primary.setEnabled(False)
            QThreadPool.globalInstance().start(runtime_worker)
        elif self._stage == "RUNTIME":
            services, _prepared = self._require_plan()
            runtime = self._runtime_state
            if services is None or runtime is None:
                self._failed("即时不可逆确认状态不完整，未清空回收站。")
                return
            try:
                services.service.resolve_empty_runtime_confirmation(runtime, True)
            except Exception as exc:
                self._failed(f"{type(exc).__name__}: {exc}")
                return
            execute_worker = RecycleBinEmptyExecuteWorker(services, runtime)
            execute_worker.signals.completed.connect(self._execution_completed)
            execute_worker.signals.failed.connect(self._failed)
            self._worker = execute_worker
            self._stage = "EXECUTING"
            self.progress.setRange(0, 0)
            self.primary.setEnabled(False)
            self.cancel.setEnabled(False)
            QThreadPool.globalInstance().start(execute_worker)
        elif self._stage in {"DONE", "FAILED"}:
            self.accept()

    @Slot(object)
    def _runtime_completed(self, value: object) -> None:
        self._worker = None
        if self._closing:
            return
        try:
            runtime = require_runtime_empty(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._runtime_state = runtime
        self._stage = "RUNTIME"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        snapshot = runtime.preview.snapshot
        self.summary.setHtml(
            "<h3>第二次确认：立即清空回收站</h3>"
            f"<p>将永久移除回收站中的 <b>{snapshot.item_count}</b> 项，"
            f"已观察大小 <b>{_format_size(snapshot.observed_size_bytes)}</b>。</p>"
            "<p><b>恢复等级 NONE：</b>Agent 无法自动或手动代您恢复。"
            "如果内容有任何变化，本确认会失效。</p>"
        )
        self.primary.setText("理解无法恢复，立即清空")
        self.primary.setEnabled(True)

    @Slot(object)
    def _execution_completed(self, value: object) -> None:
        self._worker = None
        if self._closing:
            return
        try:
            result = require_empty_result(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._stage = "DONE"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        verified = result.verification_status is (
            CleanupVerificationStatus.RECYCLE_BIN_EMPTY_VERIFIED
        )
        self.risk_label.setText("已验证回收站为空" if verified else "结果无法可靠验证")
        self.summary.setHtml(
            f"<h3>{escape(result.message)}</h3>"
            "<p>恢复等级始终为 <b>NONE</b>；本操作没有自动恢复记录。</p>"
        )
        self.primary.setText("关闭")
        self.primary.setEnabled(True)

    def _require_plan(
        self,
    ) -> tuple[SystemCleanupServices | None, PreparedRecycleBinEmpty | None]:
        if self._services is None or self._prepared is None:
            self._failed("回收站计划状态不完整，未执行清空。")
            return None, None
        return self._services, self._prepared

    @Slot(str)
    def _failed(self, message: str) -> None:
        self._worker = None
        if self._closing:
            return
        self._stage = "FAILED"
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.risk_label.setText("已安全停止，未继续清空")
        self.summary.setHtml(f"<h3>没有继续执行</h3><p>{escape(message)}</p>")
        self.primary.setText("关闭")
        self.primary.setEnabled(True)

    @Slot()
    def _cancel_clicked(self) -> None:
        cancel = getattr(self._worker, "cancel", None)
        if callable(cancel):
            cancel()
        services = self._services
        try:
            if self._stage == "PLAN" and services is not None and self._prepared is not None:
                services.service.resolve_empty_plan_confirmation(self._prepared, False)
            elif (
                self._stage == "RUNTIME"
                and services is not None
                and self._runtime_state is not None
            ):
                services.service.resolve_empty_runtime_confirmation(self._runtime_state, False)
        except Exception as exc:
            _LOG.warning("Could not persist Recycle Bin rejection: %s", type(exc).__name__)
        self.reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Cancel preparatory work before closing; execution is never killed."""
        self._closing = True
        self._cancel_clicked()
        event.accept()


def _risk_text(risk: RiskLevel) -> str:
    return "R2 HIGH IMPACT" if risk is RiskLevel.R2_HIGH_IMPACT else "R2"


def _format_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _cleanup_completion_html(result: CleanupResult) -> str:
    return (
        "<h3>受控清理结果</h3>"
        f"<p><b>成功验证：</b>{result.verified_items}；"
        f"<b>失败：</b>{result.failed_items}；"
        f"<b>因变化阻止：</b>{result.blocked_items}；"
        f"<b>未执行：</b>{result.skipped_items}。</p>"
        f"<p><b>已从原位置移走：</b>"
        f"{_format_size(result.bytes_removed_from_original_locations)}；"
        "<b>已验证释放磁盘空间：</b>未知（文件仍在回收站）。</p>"
        "<p>成功项目恢复等级为 MANUAL，需要在 Windows 回收站中手动还原。"
        "没有永久删除任何原位置对象。</p>"
    )
