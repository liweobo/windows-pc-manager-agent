"""Object-specific Stage 4C1 Preview and two-confirmation service dialog."""

from __future__ import annotations

from collections.abc import Callable
from html import escape

from PySide6.QtCore import QThreadPool, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from pc_manager_agent.app.runtime import ApplicationRuntime, ServiceActionServices
from pc_manager_agent.confirmation.service_actions import ServiceActionConfirmation
from pc_manager_agent.domain.service_actions import (
    ServiceActionPlan,
    ServiceActionPreview,
    ServiceActionResult,
    ServiceActionType,
)
from pc_manager_agent.ui.service_workers import (
    PreparedServiceAction,
    RuntimeServicePreview,
    ServiceExecutionWorker,
    ServicePrepareWorker,
    ServiceRuntimePreviewWorker,
    require_prepared_service,
    require_runtime_service,
    require_service_result,
)


class ServiceActionDialog(QDialog):
    """Keep exact identity, dependencies, approvals, steps, and final state visible."""

    completed = Signal()

    def __init__(
        self,
        runtime: ApplicationRuntime,
        action: ServiceActionType,
        *,
        service_name: str,
        display_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._action = action
        self._service_name = service_name
        self._display_name = display_name
        self._services: ServiceActionServices | None = None
        self._plan: ServiceActionPlan | None = None
        self._preview: ServiceActionPreview | None = None
        self._plan_confirmation: ServiceActionConfirmation | None = None
        self._runtime_confirmation: ServiceActionConfirmation | None = None
        self._worker: object | None = None
        self._stage = "PREPARING"
        self._cancel_callback: Callable[[], object] = self.reject
        self.setWindowTitle(f"Service {action.value}: {display_name}")
        self.resize(760, 620)
        self._build_ui()
        self._start_prepare()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._risk = QLabel("正在生成只读 Preview；尚未发送任何 SCM 控制。")
        self._risk.setWordWrap(True)
        self._details = QTextBrowser()
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        actions = QHBoxLayout()
        self._primary = QPushButton("等待 Preview")
        self._primary.setEnabled(False)
        self._cancel = QPushButton("取消，不执行")
        self._cancel.setDefault(True)
        self._cancel.clicked.connect(self._cancel_callback)
        self._primary.clicked.connect(self._advance)
        actions.addStretch(1)
        actions.addWidget(self._primary)
        actions.addWidget(self._cancel)
        layout.addWidget(self._risk)
        layout.addWidget(self._details, 1)
        layout.addWidget(self._progress)
        layout.addLayout(actions)

    def _start_prepare(self) -> None:
        worker = ServicePrepareWorker(
            self._runtime,
            f"{self._action.value.title()} Windows service {self._service_name}",
            self._service_name,
            self._action,
        )
        worker.signals.completed.connect(self._prepared)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _prepared(self, value: object) -> None:
        self._worker = None
        try:
            prepared = require_prepared_service(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._services = prepared.services
        self._plan = prepared.plan
        self._preview = prepared.preview
        if not prepared.review.approved:
            self._stage = "BLOCKED"
            self._risk.setText("安全审查已阻止；未执行任何服务操作。")
            self._details.setHtml(_preview_html(prepared))
            self._progress.setRange(0, 1)
            self._primary.setEnabled(False)
            return
        self._plan_confirmation = prepared.services.service.request_plan_confirmation(
            prepared.plan,
            prepared.preview,
        )
        self._stage = "PLAN_CONFIRMATION"
        self._risk.setText(
            f"风险 {prepared.plan.risk_level.value}；回滚 MANUAL。这是计划确认，尚未发送控制。"
        )
        self._details.setHtml(_preview_html(prepared))
        self._progress.setRange(0, 1)
        self._primary.setText("确认计划并重新验证")
        self._primary.setEnabled(True)

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
            self._failed("Service plan confirmation state is incomplete; no control was sent")
            return
        services.service.resolve_plan_confirmation(
            confirmation.confirmation_id,
            True,
            plan,
            preview,
        )
        worker = ServiceRuntimePreviewWorker(
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
            runtime = require_runtime_service(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._preview = runtime.preview
        self._runtime_confirmation = runtime.confirmation
        self._stage = "RUNTIME_CONFIRMATION"
        self._progress.setRange(0, 1)
        self._risk.setText(
            f"即时确认：将对唯一服务 {self._service_name} 执行 {self._action.value}。"
            "回滚能力 MANUAL；重启可能停在 STOPPED。"
        )
        self._details.setHtml(_runtime_html(runtime))
        self._primary.setText("即时确认并执行")
        self._primary.setEnabled(True)

    def _approve_runtime(self) -> None:
        services = self._services
        plan = self._plan
        preview = self._preview
        plan_confirmation = self._plan_confirmation
        runtime_confirmation = self._runtime_confirmation
        if (
            services is None
            or plan is None
            or preview is None
            or plan_confirmation is None
            or runtime_confirmation is None
        ):
            self._failed("Service runtime confirmation state is incomplete; no control was sent")
            return
        services.service.resolve_runtime_confirmation(
            runtime_confirmation.confirmation_id,
            True,
            plan,
            preview,
        )
        worker = ServiceExecutionWorker(
            services.service,
            plan_confirmation.confirmation_id,
            runtime_confirmation.confirmation_id,
            plan,
            preview,
        )
        worker.signals.completed.connect(self._executed)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        self._stage = "EXECUTING"
        self._primary.setEnabled(False)
        self._cancel.setText("取消后续步骤")
        self._replace_cancel_callback(worker.cancel)
        self._progress.setRange(0, 0)
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _executed(self, value: object) -> None:
        self._worker = None
        try:
            result = require_service_result(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._stage = "COMPLETED" if result.completed else "PARTIAL"
        self._progress.setRange(0, 1)
        self._progress.setValue(1)
        self._risk.setText(
            f"最终状态 {result.final_state.value}；"
            f"{'完成' if result.completed else '部分完成/已取消'}；回滚 MANUAL。"
        )
        self._details.setHtml(_result_html(result))
        self._cancel.setText("关闭")
        self._replace_cancel_callback(self.accept)
        self.completed.emit()

    @Slot(str)
    def _failed(self, message: str) -> None:
        self._worker = None
        self._stage = "FAILED"
        self._progress.setRange(0, 1)
        self._risk.setText("操作未完成；请查看错误和审计记录。")
        self._details.setPlainText(message)
        self._primary.setEnabled(False)
        self._cancel.setText("关闭")
        self._replace_cancel_callback(self.accept)

    def shutdown(self) -> None:
        """Cancel future undispatched service steps during application shutdown."""
        worker = self._worker
        if isinstance(worker, ServiceExecutionWorker):
            worker.cancel()

    def _replace_cancel_callback(self, callback: Callable[[], object]) -> None:
        self._cancel.clicked.disconnect(self._cancel_callback)
        self._cancel_callback = callback
        self._cancel.clicked.connect(self._cancel_callback)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Request cancellation before closing an active execution dialog."""
        self.shutdown()
        event.accept()


def _preview_html(prepared: PreparedServiceAction) -> str:
    preview = prepared.preview
    observation = preview.observation
    issues = "<br>".join(escape(item) for item in prepared.review.issues) or "无"
    dependencies = ", ".join(item.service_name for item in observation.dependencies) or "无"
    dependents = ", ".join(item.service_name for item in observation.dependents) or "无"
    steps = " → ".join(step.value for step in prepared.plan.steps)
    return (
        f"<h3>{escape(prepared.plan.summary)}</h3>"
        "<table border='1' cellspacing='0' cellpadding='4'>"
        f"<tr><th>Service name</th><td>{escape(observation.identity.service_name)}</td></tr>"
        f"<tr><th>显示名称</th><td>{escape(observation.identity.display_name)}</td></tr>"
        f"<tr><th>当前状态</th><td>{observation.state.value}</td></tr>"
        f"<tr><th>安全分类</th><td>{preview.safety.safety_class.value}</td></tr>"
        f"<tr><th>执行步骤</th><td>{steps}</td></tr>"
        f"<tr><th>Dependencies</th><td>{escape(dependencies)}</td></tr>"
        f"<tr><th>Running dependents</th><td>{escape(dependents)}</td></tr>"
        f"<tr><th>权限</th><td>START={preview.permissions.can_start}; "
        f"STOP={preview.permissions.can_stop}; "
        f"elevated={preview.permissions.process_elevated}</td></tr>"
        "</table>"
        f"<p>审查：{'通过' if prepared.review.approved else '阻止'}；{issues}</p>"
        "<p>不会级联启停依赖服务，不会提权，不会执行 shell。</p>"
    )


def _runtime_html(value: RuntimeServicePreview) -> str:
    preview = value.preview
    return (
        f"<h3>即时确认：{preview.action.value}</h3>"
        f"<p>唯一对象：{escape(preview.observation.identity.service_name)} / "
        f"{escape(preview.observation.identity.display_name)}</p>"
        f"<p>当前状态：{preview.observation.state.value}；"
        f"依赖摘要：{preview.dependencies.graph_digest[:12]}…；"
        f"身份摘要：{preview.observation.identity.canonical_digest()[:12]}…</p>"
        "<p>START/STOP 都不能恢复服务内部会话，因此回滚为 MANUAL。</p>"
    )


def _result_html(result: ServiceActionResult) -> str:
    rows = "".join(
        f"<li>{step.step.value}: {step.before_state.value} → {step.after_state.value}; "
        f"verified={step.verified}</li>"
        for step in result.steps
    )
    return (
        f"<h3>{result.action.value} 结果</h3>"
        f"<p>最终状态：{result.final_state.value}；{escape(result.message)}</p>"
        f"<ol>{rows}</ol>"
        "<p>如需反向操作，必须创建新的 Preview 并重新确认；这不是自动回滚。</p>"
    )
