"""Object-specific Stage 4C2 Preview, confirmation, execution, and restore history dialogs."""

from __future__ import annotations

from collections.abc import Callable
from html import escape
from uuid import UUID

from PySide6.QtCore import QThreadPool, Signal, Slot
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

from pc_manager_agent.app.runtime import ApplicationRuntime, ServiceStartupActionServices
from pc_manager_agent.confirmation.service_startup_actions import (
    ServiceStartupActionConfirmation,
)
from pc_manager_agent.domain.service_actions import ServiceStableIdentity
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionPlan,
    ServiceStartupActionPreview,
    ServiceStartupActionType,
    ServiceStartupChangeRecord,
    ServiceStartupMutationResult,
)
from pc_manager_agent.ui.service_startup_workers import (
    PreparedServiceStartupAction,
    RuntimeServiceStartupPreview,
    ServiceStartupExecutionWorker,
    ServiceStartupHistoryWorker,
    ServiceStartupPrepareWorker,
    ServiceStartupRuntimePreviewWorker,
    require_prepared_service_startup,
    require_runtime_service_startup,
    require_service_startup_history,
    require_service_startup_result,
)
from pc_manager_agent.ui.stage4x3_action_dialog import Stage4X3ActionDialog
from pc_manager_agent.ui.stage4x3_workers import Stage4X3UiAction, Stage4X3UiRequest


class ServiceStartupActionDialog(QDialog):
    """Keep source, target, backup, approvals, runtime invariant, and result visible."""

    completed = Signal()

    def __init__(
        self,
        runtime: ApplicationRuntime,
        action: ServiceStartupActionType,
        *,
        display_name: str,
        identity: object | None = None,
        restore_backup_id: UUID | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._action = action
        self._display_name = display_name
        self._identity = identity
        self._restore_backup_id = restore_backup_id
        self._services: ServiceStartupActionServices | None = None
        self._plan: ServiceStartupActionPlan | None = None
        self._preview: ServiceStartupActionPreview | None = None
        self._plan_confirmation: ServiceStartupActionConfirmation | None = None
        self._runtime_confirmation: ServiceStartupActionConfirmation | None = None
        self._worker: object | None = None
        self._stage4x3_dialog: Stage4X3ActionDialog | None = None
        self._stage = "PREPARING"
        self._cancel_callback: Callable[[], object] = self.reject
        self.setWindowTitle("Stage 4C2 服务启动类型安全变更")
        self.resize(760, 620)
        self._build_ui()
        self._prepare()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._risk = QLabel("正在只读解析服务并创建加密备份；尚未修改启动类型或运行状态。")
        self._risk.setWordWrap(True)
        self._details = QTextBrowser()
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        actions = QHBoxLayout()
        self._primary = QPushButton("等待 Preview")
        self._primary.setEnabled(False)
        self._primary.clicked.connect(self._advance)
        self._cancel = QPushButton("取消")
        self._cancel.clicked.connect(self._cancel_callback)
        actions.addWidget(self._primary)
        actions.addWidget(self._cancel)
        layout.addWidget(self._risk)
        layout.addWidget(self._details, 1)
        layout.addWidget(self._progress)
        layout.addLayout(actions)

    def _prepare(self) -> None:
        worker = ServiceStartupPrepareWorker(
            self._runtime,
            f"Change {self._display_name} startup configuration with verified backup",
            self._action,
            identity=self._identity,
            restore_backup_id=self._restore_backup_id,
        )
        worker.signals.completed.connect(self._prepared)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _prepared(self, value: object) -> None:
        self._worker = None
        try:
            prepared = require_prepared_service_startup(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._services = prepared.services
        self._plan = prepared.plan
        self._preview = prepared.preview
        self._stage = "PLAN_CONFIRMATION"
        self._progress.setRange(0, 1)
        self._details.setHtml(_preview_html(prepared))
        if prepared.review.approved:
            confirmation = prepared.services.service.request_plan_confirmation(
                prepared.plan,
                prepared.preview,
            )
            self._plan_confirmation = confirmation
            self._risk.setText(
                "计划确认：仅修改一个服务的 Automatic/Manual 启动类型。"
                "不会启停服务；条件式 FULL 恢复要求当前配置仍等于 Agent 写入值。"
            )
            self._primary.setText("批准计划并重新验证")
            self._primary.setEnabled(True)
        else:
            self._risk.setText("安全审查阻止执行；Preview 和备份记录已保留供审计。")
            self._primary.setEnabled(False)

    @Slot()
    def _advance(self) -> None:
        if self._stage == "PLAN_CONFIRMATION":
            self._approve_plan()
        elif self._stage == "RUNTIME_CONFIRMATION":
            self._approve_runtime()

    def _approve_plan(self) -> None:
        services = self._services
        plan = self._plan
        preview = self._preview
        confirmation = self._plan_confirmation
        if services is None or plan is None or preview is None or confirmation is None:
            self._failed("Plan confirmation state is incomplete; no write was sent")
            return
        services.service.resolve_plan_confirmation(
            confirmation.confirmation_id,
            True,
            plan,
            preview,
        )
        worker = ServiceStartupRuntimePreviewWorker(
            services.service,
            confirmation.confirmation_id,
            plan,
        )
        worker.signals.completed.connect(self._runtime_ready)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        self._stage = "REVALIDATING"
        self._primary.setEnabled(False)
        self._progress.setRange(0, 0)
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _runtime_ready(self, value: object) -> None:
        self._worker = None
        try:
            runtime = require_runtime_service_startup(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._preview = runtime.preview
        self._runtime_confirmation = runtime.confirmation
        self._stage = "RUNTIME_CONFIRMATION"
        self._progress.setRange(0, 1)
        self._risk.setText(
            "即时确认：将持久修改下列唯一服务的启动类型。当前运行状态必须保持不变；不会提升权限。"
        )
        self._details.setHtml(_runtime_html(runtime))
        self._primary.setText("即时确认并写入")
        self._primary.setEnabled(True)

    def _approve_runtime(self) -> None:
        services = self._services
        plan = self._plan
        preview = self._preview
        first = self._plan_confirmation
        immediate = self._runtime_confirmation
        if (
            services is None
            or plan is None
            or preview is None
            or first is None
            or immediate is None
        ):
            self._failed("Runtime confirmation state is incomplete; no write was sent")
            return
        services.service.resolve_runtime_confirmation(
            immediate.confirmation_id,
            True,
            plan,
            preview,
        )
        worker = ServiceStartupExecutionWorker(
            services.service,
            first.confirmation_id,
            immediate.confirmation_id,
            plan,
            preview,
        )
        worker.signals.completed.connect(self._executed)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        self._stage = "EXECUTING"
        self._primary.setEnabled(False)
        self._cancel.setText("取消（若尚未写入）")
        self._replace_cancel_callback(worker.cancel)
        self._progress.setRange(0, 0)
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _executed(self, value: object) -> None:
        self._worker = None
        try:
            result = require_service_startup_result(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._stage = "COMPLETED" if result.verified else "FAILED"
        self._progress.setRange(0, 1)
        self._progress.setValue(1)
        self._risk.setText(
            f"配置验证={'通过' if result.verified else '失败'}；"
            f"运行状态不变={'是' if result.runtime_unchanged else '否'}。"
        )
        self._details.setHtml(_result_html(result))
        self._cancel.setText("关闭")
        self._replace_cancel_callback(self.accept)
        if result.verified:
            self.completed.emit()

    @Slot(str)
    def _failed(self, message: str) -> None:
        self._worker = None
        if self._can_offer_stage4x3(message):
            self._open_stage4x3()
            return
        self._stage = "FAILED"
        self._progress.setRange(0, 1)
        self._risk.setText("启动类型操作未完成；不会自动重试或提升权限。")
        self._details.setPlainText(message)
        self._primary.setEnabled(False)
        self._cancel.setText("关闭")
        self._replace_cancel_callback(self.accept)

    def _can_offer_stage4x3(self, message: str) -> bool:
        return bool(
            self._stage == "PREPARING"
            and "PRIVILEGE_REQUIRED" in message
            and self._runtime.settings.privileged_broker_mode == "windows"
            and (self._action is ServiceStartupActionType.RESTORE or self._identity is not None)
        )

    def _open_stage4x3(self) -> None:
        """Hand a permission-only Stage 4C2 denial to its dedicated R3 workflow."""
        action = (
            Stage4X3UiAction.SERVICE_STARTUP_RESTORE
            if self._action is ServiceStartupActionType.RESTORE
            else Stage4X3UiAction.SERVICE_STARTUP_CHANGE
        )
        identity = (
            None
            if self._action is ServiceStartupActionType.RESTORE
            else ServiceStableIdentity.model_validate(self._identity)
        )
        request = Stage4X3UiRequest(
            action=action,
            user_goal=f"更改 {self._display_name} 的服务启动类型",
            display_name=self._display_name,
            service_identity=identity,
            service_action=self._action,
            backup_id=self._restore_backup_id,
        )
        dialog = Stage4X3ActionDialog(self._runtime, request, parent=self)
        dialog.completed.connect(self.completed.emit)
        dialog.finished.connect(lambda _result: self.accept())
        self._stage4x3_dialog = dialog
        self._stage = "ELEVATED_HANDOFF"
        self._progress.setRange(0, 1)
        self._risk.setText(
            "普通用户权限不足；已打开独立 Stage 4X3 管理员流程。原 R2 确认不会被复用。"
        )
        self._details.setPlainText(
            "管理员流程会重新创建 R3 计划、重新验证备份，并要求计划确认、即时确认和 Windows UAC。"
        )
        self._primary.setEnabled(False)
        self._cancel.setText("关闭")
        self._replace_cancel_callback(self.accept)
        dialog.show()

    def shutdown(self) -> None:
        """Cancel a not-yet-dispatched configuration write during application shutdown."""
        if isinstance(self._worker, ServiceStartupExecutionWorker):
            self._worker.cancel()
        if self._stage4x3_dialog is not None:
            self._stage4x3_dialog.shutdown()

    def _replace_cancel_callback(self, callback: Callable[[], object]) -> None:
        self._cancel.clicked.disconnect(self._cancel_callback)
        self._cancel_callback = callback
        self._cancel.clicked.connect(self._cancel_callback)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Request cancellation before closing an active configuration dialog."""
        self.shutdown()
        event.accept()


class ServiceStartupHistoryDialog(QDialog):
    """Show bounded Agent-owned change history and open fresh restore Previews."""

    restore_requested = Signal(object, str)

    def __init__(
        self,
        runtime: ApplicationRuntime,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._worker: ServiceStartupHistoryWorker | None = None
        self._records: tuple[ServiceStartupChangeRecord, ...] = ()
        self.setWindowTitle("服务启动类型恢复历史")
        self.resize(720, 420)
        layout = QVBoxLayout(self)
        info = QLabel("恢复前会重新读取服务；只有当前配置精确等于 Agent 写入值时才能继续。")
        info.setWordWrap(True)
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ("Service", "原配置", "Agent 写入配置", "变更时间", "备份 ID")
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._restore = QPushButton("为选中记录生成恢复 Preview")
        self._restore.setEnabled(False)
        self._restore.clicked.connect(self._request_restore)
        self._table.itemSelectionChanged.connect(
            lambda: self._restore.setEnabled(self._selected() is not None)
        )
        layout.addWidget(info)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._restore)
        self._load()

    def _load(self) -> None:
        worker = ServiceStartupHistoryWorker(self._runtime)
        worker.signals.completed.connect(self._loaded)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _loaded(self, value: object) -> None:
        self._worker = None
        try:
            self._records = require_service_startup_history(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._table.setRowCount(len(self._records))
        for row, record in enumerate(self._records):
            values = (
                f"{record.display_name} ({record.stable_identity.service_name})",
                record.original_configuration.startup_type.value,
                record.written_configuration.startup_type.value,
                record.changed_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                str(record.backup_id),
            )
            for column, text in enumerate(values):
                cell = QTableWidgetItem(text)
                cell.setData(256, record)
                self._table.setItem(row, column, cell)

    @Slot(str)
    def _failed(self, message: str) -> None:
        self._worker = None
        self._table.setRowCount(1)
        self._table.setItem(0, 0, QTableWidgetItem(f"无法读取恢复历史：{message}"))

    def _selected(self) -> ServiceStartupChangeRecord | None:
        cells = self._table.selectedItems()
        if not cells:
            return None
        value = cells[0].data(256)
        return value if isinstance(value, ServiceStartupChangeRecord) else None

    @Slot()
    def _request_restore(self) -> None:
        record = self._selected()
        if record is None:
            return
        self.restore_requested.emit(record.backup_id, record.display_name)
        self.accept()


def _preview_html(prepared: PreparedServiceStartupAction) -> str:
    preview = prepared.preview
    plan = prepared.plan
    issues = "<br>".join(escape(item) for item in prepared.review.issues) or "无"
    return (
        f"<h3>{escape(plan.summary)}</h3>"
        "<table border='1' cellspacing='0' cellpadding='4'>"
        f"<tr><th>Service name</th><td>{escape(plan.target_identity.service_name)}</td></tr>"
        f"<tr><th>显示名称</th><td>{escape(plan.display_name)}</td></tr>"
        f"<tr><th>当前启动类型</th><td>{plan.source_configuration.startup_type.value}</td></tr>"
        f"<tr><th>目标启动类型</th><td>{plan.target_configuration.startup_type.value}</td></tr>"
        f"<tr><th>当前运行状态</th><td>{preview.observation.state.value}</td></tr>"
        f"<tr><th>安全分类</th><td>{preview.safety.safety_class.value}</td></tr>"
        f"<tr><th>依赖影响</th><td>{escape(preview.impact.summary)}</td></tr>"
        f"<tr><th>现有权限</th><td>query={preview.permissions.can_query_configuration}; "
        f"change={preview.permissions.can_change_configuration}; "
        f"elevated={preview.permissions.process_elevated}</td></tr>"
        f"<tr><th>备份</th><td>{plan.backup_id}; verified={preview.backup_verified}</td></tr>"
        "</table>"
        f"<p>审查：{'通过' if prepared.review.approved else '阻止'}；{issues}</p>"
        "<p>不会调用 START/STOP，不修改延迟启动、Disabled、依赖或其他服务字段。</p>"
    )


def _runtime_html(value: RuntimeServiceStartupPreview) -> str:
    preview = value.preview
    return (
        f"<h3>即时确认：{preview.action.value}</h3>"
        f"<p>唯一对象：{escape(preview.observation.identity.service_name)} / "
        f"{escape(preview.observation.display_name)}</p>"
        f"<p>{preview.observation.startup_configuration.startup_type.value} → "
        f"{preview.target_configuration.startup_type.value}</p>"
        f"<p>当前运行状态必须保持 {preview.observation.state.value}；"
        f"备份 {preview.backup_id} 已验证。</p>"
        "<p>恢复为条件式 FULL：外部配置发生变化时会报告 RESTORE_CONFLICT。</p>"
    )


def _result_html(result: ServiceStartupMutationResult) -> str:
    return (
        f"<h3>{result.action.value} 结果</h3>"
        f"<p>{result.before_configuration.startup_type.value} → "
        f"{result.after_configuration.startup_type.value}</p>"
        f"<p>运行状态：{result.before_runtime_state.value} → "
        f"{result.after_runtime_state.value}；unchanged={result.runtime_unchanged}</p>"
        f"<p>verified={result.verified}；{escape(result.message)}</p>"
        "<p>如需恢复，请从恢复历史创建新的 Preview 并完成两次确认。</p>"
    )
