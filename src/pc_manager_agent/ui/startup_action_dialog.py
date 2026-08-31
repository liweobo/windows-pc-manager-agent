"""Object-specific Stage 4B Preview and two-confirmation startup dialog."""

from __future__ import annotations

from html import escape
from uuid import UUID

from PySide6.QtCore import QThreadPool, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from pc_manager_agent.app.runtime import ApplicationRuntime, StartupActionServices
from pc_manager_agent.confirmation.startup_actions import StartupActionConfirmation
from pc_manager_agent.domain.optimization_receipts import OptimizationReceiptKind
from pc_manager_agent.domain.startup_actions import (
    StartupActionPlan,
    StartupActionPreview,
    StartupActionType,
    StartupIdentity,
    StartupMutationResult,
)
from pc_manager_agent.safety.startup_validator import StartupSafetyReview
from pc_manager_agent.ui.domain_review_events import ObservedDomainDialog
from pc_manager_agent.ui.startup_workers import (
    StartupExecutionWorker,
    StartupPrepareWorker,
    StartupRuntimePreviewWorker,
    require_prepared_startup,
    require_runtime_startup,
    require_startup_result,
)


class StartupActionDialog(ObservedDomainDialog):
    """Keep backup, Preview, both approvals, execution, and verification visible."""

    completed = Signal()

    def __init__(
        self,
        runtime: ApplicationRuntime,
        action: StartupActionType,
        *,
        identity: StartupIdentity | None = None,
        backup_id: UUID | None = None,
        display_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._action = action
        self._display_name = display_name
        self._identity = identity
        self._backup_id = backup_id
        self._services: StartupActionServices | None = None
        self._plan: StartupActionPlan | None = None
        self._preview: StartupActionPreview | None = None
        self._plan_confirmation: StartupActionConfirmation | None = None
        self._runtime_confirmation: StartupActionConfirmation | None = None
        self._worker: object | None = None
        self._stage = "PREPARING"
        self._discard_after_worker = False
        self.setWindowTitle("启动项安全管理 — Preview 与双重确认")
        self.resize(820, 620)
        self.setModal(False)
        self._build_ui()
        self._start_prepare()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._risk = QLabel("正在重新读取启动项并创建加密备份；此阶段不会修改启动配置。")
        self._risk.setStyleSheet("font-weight: 700;")
        self._details = QTextBrowser()
        self._details.setOpenExternalLinks(False)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        row = QHBoxLayout()
        self._primary = QPushButton("等待 Preview")
        self._cancel = QPushButton("取消（默认）")
        self._primary.setEnabled(False)
        self._primary.setAutoDefault(False)
        self._cancel.setDefault(True)
        self._cancel.setAutoDefault(True)
        self._primary.clicked.connect(self._primary_clicked)
        self._cancel.clicked.connect(self._cancel_clicked)
        row.addStretch(1)
        row.addWidget(self._primary)
        row.addWidget(self._cancel)
        layout.addWidget(self._risk)
        layout.addWidget(self._details, 1)
        layout.addWidget(self._progress)
        layout.addLayout(row)

    def _start_prepare(self) -> None:
        worker = StartupPrepareWorker(
            self._runtime,
            (
                f"Disable startup entry {self._display_name}"
                if self._action is StartupActionType.DISABLE
                else f"Restore startup entry {self._display_name}"
            ),
            self._action,
            identity=self._identity,
            backup_id=self._backup_id,
        )
        worker.signals.completed.connect(self._prepared)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _prepared(self, value: object) -> None:
        self._worker = None
        try:
            prepared = require_prepared_startup(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._services = prepared.services
        self._plan = prepared.plan
        self.publish_domain_preview(OptimizationReceiptKind.STARTUP, prepared.plan.transaction_id)
        self._preview = prepared.preview
        self._show_preview(prepared.plan, prepared.preview, prepared.review, runtime=False)
        if not prepared.review.approved:
            self._stage = "BLOCKED"
            self._risk.setText("安全策略已阻止：未执行任何启动项修改。")
            self._cancel.setText("关闭")
            return
        try:
            request = prepared.services.service.request_plan_confirmation(
                prepared.plan,
                prepared.preview,
            )
        except Exception as exc:
            self._failed(f"{type(exc).__name__}: {exc}")
            return
        self._plan_confirmation = request
        self._stage = "PLAN_CONFIRMATION"
        self._risk.setText(_risk_text(self._action, runtime=False))
        self._primary.setText("确认计划并重新验证")
        self._primary.setEnabled(True)
        if self._discard_after_worker:
            self._reject_plan_and_close()

    @Slot()
    def _primary_clicked(self) -> None:
        if self._stage == "PLAN_CONFIRMATION":
            self._approve_plan()
        elif self._stage == "RUNTIME_CONFIRMATION":
            self._approve_runtime()
        elif self._stage in {"COMPLETED", "FAILED", "BLOCKED"}:
            self.accept()

    def _approve_plan(self) -> None:
        if not self._has_plan_state() or self._plan_confirmation is None:
            self._failed("确认状态不完整；操作未执行。请关闭后刷新启动项。")
            return
        services, plan, preview = self._require_plan_state()
        try:
            services.service.resolve_plan_confirmation(
                self._plan_confirmation.confirmation_id,
                True,
                plan,
                preview,
            )
        except Exception as exc:
            self._failed(f"{type(exc).__name__}: {exc}")
            return
        self._stage = "RUNTIME_REVALIDATING"
        self._set_busy("正在重新读取身份、配置状态和加密备份；尚未执行写操作。")
        worker = StartupRuntimePreviewWorker(
            services.service,
            self._plan_confirmation.confirmation_id,
            plan,
        )
        worker.signals.completed.connect(self._runtime_prepared)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _runtime_prepared(self, value: object) -> None:
        self._worker = None
        try:
            prepared = require_runtime_startup(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        if self._plan is None:
            self._failed("Internal state error: startup plan is unavailable")
            return
        self._preview = prepared.preview
        self._runtime_confirmation = prepared.confirmation
        self._stage = "RUNTIME_CONFIRMATION"
        self._show_preview(
            self._plan,
            prepared.preview,
            StartupSafetyReview(approved=True),
            runtime=True,
        )
        self._risk.setText(_risk_text(self._action, runtime=True))
        self._primary.setText(
            "立即停用此启动项" if self._action is StartupActionType.DISABLE else "立即恢复此启动项"
        )
        self._primary.setEnabled(True)
        if self._discard_after_worker:
            self._reject_runtime_and_close()

    def _approve_runtime(self) -> None:
        if (
            not self._has_plan_state()
            or self._plan_confirmation is None
            or self._runtime_confirmation is None
        ):
            self._failed("即时确认状态不完整；操作未执行。请关闭后刷新启动项。")
            return
        services, plan, preview = self._require_plan_state()
        try:
            services.service.resolve_runtime_confirmation(
                self._runtime_confirmation.confirmation_id,
                True,
                plan,
                preview,
            )
        except Exception as exc:
            self._failed(f"{type(exc).__name__}: {exc}")
            return
        worker = StartupExecutionWorker(
            services.service,
            self._plan_confirmation.confirmation_id,
            self._runtime_confirmation.confirmation_id,
            plan,
            preview,
        )
        worker.signals.completed.connect(self._completed)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        self._stage = "EXECUTING"
        self._set_busy("双重确认已消费；正在执行窄工具并重新读取配置验证结果。")
        self._cancel.setEnabled(False)
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _completed(self, value: object) -> None:
        self._worker = None
        self._cancel.setEnabled(True)
        try:
            result = require_startup_result(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._stage = "COMPLETED"
        self._progress.setRange(0, 1)
        self._progress.setValue(1 if result.verified else 0)
        self._risk.setText(
            "配置状态已重新读取并记录审计。回滚等级 FULL；这不代表程序下次一定会或不会启动。"
        )
        self._details.setHtml(_result_html(result))
        self._primary.setText("关闭")
        self._primary.setEnabled(True)
        self._cancel.setText("关闭")
        self.completed.emit()

    def _show_preview(
        self,
        plan: StartupActionPlan,
        preview: StartupActionPreview,
        review: StartupSafetyReview,
        *,
        runtime: bool,
    ) -> None:
        self._progress.setRange(0, 1)
        self._progress.setValue(1)
        self._details.setHtml(_preview_html(plan, preview, review, runtime=runtime))

    def _has_plan_state(self) -> bool:
        return self._services is not None and self._plan is not None and self._preview is not None

    @Slot()
    def _cancel_clicked(self) -> None:
        if self._worker is not None:
            self._discard_after_worker = True
            self._cancel.setEnabled(False)
            self._risk.setText("正在安全结束只读准备或重验；完成后会拒绝待确认请求。")
            return
        if self._stage == "PLAN_CONFIRMATION":
            self._reject_plan_and_close()
        elif self._stage == "RUNTIME_CONFIRMATION":
            self._reject_runtime_and_close()
        else:
            self.reject()

    def _reject_plan_and_close(self) -> None:
        if not self._has_plan_state() or self._plan_confirmation is None:
            self.reject()
            return
        services, plan, preview = self._require_plan_state()
        try:
            services.service.resolve_plan_confirmation(
                self._plan_confirmation.confirmation_id,
                False,
                plan,
                preview,
            )
        finally:
            self.reject()

    def _reject_runtime_and_close(self) -> None:
        if not self._has_plan_state() or self._runtime_confirmation is None:
            self.reject()
            return
        services, plan, preview = self._require_plan_state()
        try:
            services.service.resolve_runtime_confirmation(
                self._runtime_confirmation.confirmation_id,
                False,
                plan,
                preview,
            )
        finally:
            self.reject()

    @Slot(str)
    def _failed(self, message: str) -> None:
        self._worker = None
        self._stage = "FAILED"
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._risk.setText("操作未执行或结果无法确认。请刷新启动项，不要重复猜测。")
        self._details.setPlainText(message)
        self._primary.setText("关闭")
        self._primary.setEnabled(True)
        self._cancel.setEnabled(True)
        self._cancel.setText("关闭")

    def _set_busy(self, text: str) -> None:
        self._risk.setText(text)
        self._progress.setRange(0, 0)
        self._primary.setEnabled(False)

    def _require_plan_state(
        self,
    ) -> tuple[StartupActionServices, StartupActionPlan, StartupActionPreview]:
        """Return complete dialog state or fail closed without relying on assertions."""
        if self._services is None or self._plan is None or self._preview is None:
            raise RuntimeError("Startup action dialog state is incomplete")
        return self._services, self._plan, self._preview

    def closeEvent(self, event: QCloseEvent) -> None:
        """Never abandon a pending confirmation or background mutation."""
        if self._worker is not None or self._stage in {
            "PLAN_CONFIRMATION",
            "RUNTIME_CONFIRMATION",
        }:
            event.ignore()
            self._cancel_clicked()
            return
        event.accept()

    def shutdown(self) -> None:
        """Reject pending confirmation after any active bounded worker returns."""
        if self._worker is not None:
            self._discard_after_worker = True


def _risk_text(action: StartupActionType, *, runtime: bool) -> str:
    phase = "即时确认" if runtime else "计划确认"
    verb = "停用" if action is StartupActionType.DISABLE else "恢复"
    return f"{phase}：R2 单对象{verb}；普通用户权限；精确加密备份；回滚 FULL；冲突时不覆盖。"


def _preview_html(
    plan: StartupActionPlan,
    preview: StartupActionPreview,
    review: StartupSafetyReview,
    *,
    runtime: bool,
) -> str:
    item = preview.observation
    issues = "<br>".join(escape(value) for value in review.issues) or "无"
    title = "即时重验 Preview" if runtime else "计划 Preview"
    return (
        f"<h3>{title}</h3>"
        f"<p>动作：<b>{plan.action.value}</b>；风险：<b>R2</b>；对象：1；"
        "管理员权限：否；回滚：<b>FULL</b>。</p>"
        "<p>停用不是永久删除。恢复只使用 Agent 创建并验证的加密备份；冲突时停止。</p>"
        "<table border='1' cellspacing='0' cellpadding='4'>"
        f"<tr><th>名称</th><td>{escape(item.display_name)}</td></tr>"
        f"<tr><th>发布者</th><td>{escape(item.publisher or '未知')}</td></tr>"
        f"<tr><th>来源</th><td>{item.identity.source.value}</td></tr>"
        f"<tr><th>范围</th><td>{item.scope}</td></tr>"
        f"<tr><th>当前配置</th><td>{item.status.value}</td></tr>"
        f"<tr><th>目标程序</th><td>{escape(str(item.executable_path or '未解析'))}</td></tr>"
        f"<tr><th>安全分类</th><td>{preview.assessment.safety_class.value}</td></tr>"
        f"<tr><th>备份</th><td>{'已验证' if preview.backup_verified else '不可用'}"
        f"（摘要 {preview.backup_digest[:12]}…）</td></tr></table>"
        f"<p>安全审查：{'通过' if review.approved else '阻止'}；问题：{issues}</p>"
        "<p>说明：验证的是启动配置状态，不保证程序下一次登录时一定会或不会启动。</p>"
    )


def _result_html(result: StartupMutationResult) -> str:
    return (
        f"<h3>配置验证结果：{result.action.value}</h3>"
        f"<p>状态：{result.after_status.value}；验证：{'通过' if result.verified else '失败'}。</p>"
        f"<p>{escape(result.message)}</p>"
        f"<p>自动反向操作：{'已执行' if result.rollback_performed else '未触发'}；"
        f"反向验证：{'通过' if result.rollback_verified else '不适用'}。</p>"
    )
