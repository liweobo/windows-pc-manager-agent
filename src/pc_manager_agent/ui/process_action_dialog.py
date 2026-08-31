"""Object-specific Stage 4A Preview and two-confirmation process dialog."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QThreadPool, Slot
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

from pc_manager_agent.app.runtime import ApplicationRuntime, ProcessActionServices
from pc_manager_agent.confirmation.process_actions import ProcessActionConfirmation
from pc_manager_agent.domain.optimization_receipts import OptimizationReceiptKind
from pc_manager_agent.domain.process_actions import (
    ProcessActionErrorCode,
    ProcessActionPlan,
    ProcessActionPreview,
    ProcessActionToolResult,
    ProcessActionType,
    ProcessMemberResultState,
    ProcessSafetyDecision,
    ProcessTargetQuery,
)
from pc_manager_agent.safety.process_validator import ProcessSafetyReview
from pc_manager_agent.ui.domain_review_events import ObservedDomainDialog
from pc_manager_agent.ui.process_workers import (
    ProcessExecutionWorker,
    ProcessForcePreviewWorker,
    ProcessPrepareWorker,
    ProcessRuntimePreviewWorker,
    require_prepared_process_action,
    require_process_result,
    require_runtime_process_preview,
)


class ProcessActionDialog(ObservedDomainDialog):
    """Keep Preview, both approvals, execution, verification, and force flow visible."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        user_goal: str,
        *,
        query: ProcessTargetQuery | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._user_goal = user_goal
        self._query = query
        self._services: ProcessActionServices | None = None
        self._plan: ProcessActionPlan | None = None
        self._preview: ProcessActionPreview | None = None
        self._plan_confirmation: ProcessActionConfirmation | None = None
        self._runtime_confirmation: ProcessActionConfirmation | None = None
        self._worker: object | None = None
        self._execution_worker: ProcessExecutionWorker | None = None
        self._stage = "PREPARING"
        self._discard_after_worker = False
        self.setWindowTitle("受控进程关闭 — Preview 与双确认")
        self.resize(900, 680)
        self.setModal(False)
        self._build_ui()
        self._start_prepare()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._risk = QLabel("正在读取实时进程身份；此步骤只读。")
        self._risk.setStyleSheet("font-weight: 700;")
        self._details = QTextBrowser()
        self._details.setOpenExternalLinks(False)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        buttons = QHBoxLayout()
        self._force_button = QPushButton("查看强制终止选项")
        self._primary_button = QPushButton("等待 Preview")
        self._cancel_button = QPushButton("取消（默认）")
        self._force_button.setEnabled(False)
        self._primary_button.setEnabled(False)
        self._cancel_button.setDefault(True)
        self._cancel_button.setAutoDefault(True)
        self._primary_button.setAutoDefault(False)
        self._force_button.setAutoDefault(False)
        self._force_button.clicked.connect(self._start_force_preview)
        self._primary_button.clicked.connect(self._primary_clicked)
        self._cancel_button.clicked.connect(self._cancel_clicked)
        buttons.addWidget(self._force_button)
        buttons.addStretch(1)
        buttons.addWidget(self._primary_button)
        buttons.addWidget(self._cancel_button)
        layout.addWidget(self._risk)
        layout.addWidget(self._details, 1)
        layout.addWidget(self._progress)
        layout.addLayout(buttons)

    def _start_prepare(self) -> None:
        worker = ProcessPrepareWorker(
            self._runtime,
            self._user_goal,
            query=self._query,
            action=(ProcessActionType.REQUEST_GRACEFUL_EXIT if self._query is not None else None),
        )
        worker.signals.completed.connect(self._prepared)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _prepared(self, value: object) -> None:
        self._worker = None
        try:
            prepared = require_prepared_process_action(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._services = prepared.services
        self._show_prepared(prepared.plan, prepared.preview, prepared.review)

    def _show_prepared(
        self,
        plan: ProcessActionPlan,
        preview: ProcessActionPreview,
        review: ProcessSafetyReview,
    ) -> None:
        self._plan = plan
        self.publish_domain_preview(OptimizationReceiptKind.PROCESS, plan.transaction_id)
        self._preview = preview
        self._runtime_confirmation = None
        self._progress.setRange(0, 1)
        self._progress.setValue(1)
        self._details.setHtml(_preview_html(plan, preview, review, runtime=False))
        if not review.approved:
            self._stage = "BLOCKED"
            self._risk.setText("安全策略已阻止：未执行任何进程操作。")
            self._primary_button.setEnabled(False)
            self._force_button.setEnabled(_force_is_valid_alternative(plan, preview))
            self._cancel_button.setText("关闭")
            if self._discard_after_worker:
                self.reject()
            return
        services = self._services
        if services is None:
            self._failed("Internal state error: process services are unavailable")
            return
        try:
            confirmation = services.service.request_plan_confirmation(plan, preview)
        except Exception as exc:
            self._failed(f"{type(exc).__name__}: {exc}")
            return
        self._plan_confirmation = confirmation
        self._stage = "PLAN_CONFIRMATION"
        self._risk.setText(_risk_text(plan.action, runtime=False))
        self._primary_button.setText("确认计划并重新验证")
        self._primary_button.setEnabled(True)
        self._force_button.setEnabled(False)
        if self._discard_after_worker:
            self._reject_plan_and_close()

    @Slot()
    def _primary_clicked(self) -> None:
        if self._stage == "PLAN_CONFIRMATION":
            self._approve_plan()
        elif self._stage == "RUNTIME_CONFIRMATION":
            self._approve_runtime_and_execute()
        elif self._stage in {"COMPLETED", "FAILED", "BLOCKED"}:
            self.accept()

    def _approve_plan(self) -> None:
        services = self._services
        plan = self._plan
        preview = self._preview
        confirmation = self._plan_confirmation
        if services is None or plan is None or preview is None or confirmation is None:
            return
        try:
            services.service.resolve_plan_confirmation(
                confirmation.confirmation_id,
                True,
                plan,
                preview,
            )
        except Exception as exc:
            self._failed(f"{type(exc).__name__}: {exc}")
            return
        self._stage = "RUNTIME_REVALIDATING"
        self._set_busy("正在执行即时身份和安全重验证；尚未发送退出请求。")
        worker = ProcessRuntimePreviewWorker(
            services.service,
            confirmation.confirmation_id,
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
            prepared = require_runtime_process_preview(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        plan = self._plan
        if plan is None:
            self._failed("Internal state error: the process plan is unavailable")
            return
        self._preview = prepared.preview
        self._runtime_confirmation = prepared.confirmation
        self._stage = "RUNTIME_CONFIRMATION"
        self._progress.setRange(0, 1)
        self._progress.setValue(1)
        self._risk.setText(_risk_text(plan.action, runtime=True))
        self._details.setHtml(
            _preview_html(
                plan,
                prepared.preview,
                ProcessSafetyReview(approved=True, issues=()),
                runtime=True,
            )
        )
        self._primary_button.setText(
            "请求正常退出"
            if plan.action is ProcessActionType.REQUEST_GRACEFUL_EXIT
            else "确认强制终止（不可撤销）"
        )
        self._primary_button.setEnabled(True)
        if self._discard_after_worker:
            self._reject_runtime_and_close()

    def _approve_runtime_and_execute(self) -> None:
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
            return
        try:
            services.service.resolve_runtime_confirmation(
                runtime_confirmation.confirmation_id,
                True,
                plan,
                preview,
            )
        except Exception as exc:
            self._failed(f"{type(exc).__name__}: {exc}")
            return
        worker = ProcessExecutionWorker(
            services.service,
            plan_confirmation.confirmation_id,
            runtime_confirmation.confirmation_id,
            plan,
            preview,
        )
        worker.signals.completed.connect(self._completed)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        self._execution_worker = worker
        self._stage = "EXECUTING"
        self._set_busy(
            "已消费双重确认；正在请求正常退出并验证结果。"
            if plan.action is ProcessActionType.REQUEST_GRACEFUL_EXIT
            else "已消费新的双重确认；正在强制终止并验证结果。"
        )
        self._cancel_button.setText(
            "停止等待（不是撤销）"
            if plan.action is ProcessActionType.REQUEST_GRACEFUL_EXIT
            else "停止后续对象（不是撤销）"
        )
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _completed(self, value: object) -> None:
        self._worker = None
        self._execution_worker = None
        try:
            result = require_process_result(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        plan = self._plan
        if plan is None:
            self._failed("Internal state error: the completed process plan is unavailable")
            return
        self._stage = "COMPLETED"
        self._progress.setRange(0, 1)
        self._progress.setValue(1)
        self._details.setHtml(_result_html(plan, result))
        self._risk.setText("结果已逐个验证并写入审计。回滚等级 NONE；重新启动程序不等于 Undo。")
        self._primary_button.setText("关闭")
        self._primary_button.setEnabled(True)
        self._cancel_button.setText("关闭")
        can_force = plan.action is ProcessActionType.REQUEST_GRACEFUL_EXIT and any(
            item.state
            in {
                ProcessMemberResultState.STILL_RUNNING,
                ProcessMemberResultState.UNSUPPORTED,
            }
            for item in result.members
        )
        self._force_button.setEnabled(can_force)

    @Slot()
    def _start_force_preview(self) -> None:
        if self._services is None or self._plan is None or self._worker is not None:
            return
        self._stage = "FORCE_PREPARING"
        self._set_busy("正在创建全新的强制终止 Preview；旧确认不会复用。")
        self._force_button.setEnabled(False)
        worker = ProcessForcePreviewWorker(self._services.service, self._plan)
        worker.signals.completed.connect(self._force_prepared)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _force_prepared(self, value: object) -> None:
        self._worker = None
        if not isinstance(value, tuple) or len(value) != 3:
            self._failed("Worker emitted an invalid force Preview")
            return
        plan, preview, review = value
        if (
            not isinstance(plan, ProcessActionPlan)
            or not isinstance(preview, ProcessActionPreview)
            or not isinstance(review, ProcessSafetyReview)
        ):
            self._failed("Worker emitted an invalid force Preview")
            return
        self._plan_confirmation = None
        self._show_prepared(plan, preview, review)

    @Slot()
    def _cancel_clicked(self) -> None:
        if self._execution_worker is not None:
            self._execution_worker.cancel()
            self._risk.setText(
                "已请求停止等待/后续对象；已发送的 WM_CLOSE 或 TerminateProcess 无法撤销。"
            )
            return
        if self._worker is not None:
            self._discard_after_worker = True
            self._cancel_button.setEnabled(False)
            self._risk.setText("正在安全结束只读重验证；完成后会拒绝任何待确认请求。")
            return
        if self._stage == "PLAN_CONFIRMATION":
            self._reject_plan_and_close()
            return
        if self._stage == "RUNTIME_CONFIRMATION":
            self._reject_runtime_and_close()
            return
        self.reject()

    def _reject_plan_and_close(self) -> None:
        services = self._services
        plan = self._plan
        preview = self._preview
        confirmation = self._plan_confirmation
        if services is None or plan is None or preview is None or confirmation is None:
            self.reject()
            return
        try:
            services.service.resolve_plan_confirmation(
                confirmation.confirmation_id,
                False,
                plan,
                preview,
            )
        finally:
            self.reject()

    def _reject_runtime_and_close(self) -> None:
        services = self._services
        plan = self._plan
        preview = self._preview
        confirmation = self._runtime_confirmation
        if services is None or plan is None or preview is None or confirmation is None:
            self.reject()
            return
        try:
            services.service.resolve_runtime_confirmation(
                confirmation.confirmation_id,
                False,
                plan,
                preview,
            )
        finally:
            self.reject()

    @Slot(str)
    def _failed(self, message: str) -> None:
        self._worker = None
        self._execution_worker = None
        self._stage = "FAILED"
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._risk.setText("操作未执行或结果不能确认；请阅读错误，不要重复猜测。")
        self._details.setPlainText(message)
        self._primary_button.setText("关闭")
        self._primary_button.setEnabled(True)
        self._force_button.setEnabled(False)
        self._cancel_button.setText("关闭")

    def _set_busy(self, text: str) -> None:
        self._risk.setText(text)
        self._progress.setRange(0, 0)
        self._primary_button.setEnabled(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Never abandon an outstanding confirmation or uncontrolled worker."""
        if self._worker is not None:
            event.ignore()
            self._cancel_clicked()
            return
        if self._stage in {"PLAN_CONFIRMATION", "RUNTIME_CONFIRMATION"}:
            event.ignore()
            self._cancel_clicked()
            return
        event.accept()

    def shutdown(self) -> None:
        """Request bounded cooperative cancellation during application exit."""
        if self._execution_worker is not None:
            self._execution_worker.cancel()
        elif self._worker is not None:
            self._discard_after_worker = True


def _risk_text(action: ProcessActionType, *, runtime: bool) -> str:
    phase = "即时确认" if runtime else "计划确认"
    if action is ProcessActionType.REQUEST_GRACEFUL_EXIT:
        return f"{phase}：R2 正常退出；可能丢失未保存数据；回滚 NONE；不提升权限。"
    return (
        f"{phase}：R2_HIGH_IMPACT 强制终止；可能丢失未保存数据或损坏应用状态；"
        "回滚 NONE；不提升权限。"
    )


def _preview_html(
    plan: ProcessActionPlan,
    preview: ProcessActionPreview,
    review: ProcessSafetyReview,
    *,
    runtime: bool,
) -> str:
    title = "即时重验证 Preview" if runtime else "计划 Preview"
    rows: list[str] = []
    assessment_by_digest = {item.identity_digest: item for item in preview.assessments}
    for target in preview.targets:
        group = escape(target.display_name)
        for member in target.members:
            identity = member.identity
            assessment = assessment_by_digest[identity.canonical_digest()]
            reason = ", ".join(value.value for value in assessment.reason_codes) or "ALLOW"
            rows.append(
                "<tr>"
                f"<td>{group}</td><td>{identity.pid}</td>"
                f"<td>{escape(identity.process_name)}</td>"
                f"<td>{escape(str(identity.executable_path))}</td>"
                f"<td>{escape(identity.username or identity.owner_sid)}</td>"
                f"<td>{identity.create_time.isoformat()}</td>"
                f"<td>{assessment.safety_class.value}</td>"
                f"<td>{assessment.decision.value}: {escape(reason)}</td>"
                "</tr>"
            )
    issues = "<br>".join(escape(item) for item in review.issues) or "无"
    warning = (
        "<p><b>强警告：</b>强制终止不会让应用保存数据，可能造成未保存内容丢失或应用状态损坏。</p>"
        if plan.action is ProcessActionType.FORCE_TERMINATE
        else "<p><b>提示：</b>正常退出也可能触发应用自己的未保存数据提示。</p>"
    )
    return (
        f"<h3>{title}</h3>"
        f"<p>动作：<b>{plan.action.value}</b>；风险：<b>{plan.risk_level.value}</b>；"
        f"应用数：{preview.application_count}；进程数：{preview.process_count}；"
        f"内存合计：{preview.total_memory_rss_bytes / 1024 / 1024:.1f} MiB；"
        f"CPU 合计：{preview.total_cpu_percent:.1f}%</p>"
        f"{warning}"
        "<p>权限：普通用户；管理员权限：否；回滚等级：<b>NONE</b>；自动恢复：不支持。</p>"
        f"<p>安全审查：{'通过' if review.approved else '阻止'}；问题：{issues}</p>"
        "<table border='1' cellspacing='0' cellpadding='4'>"
        "<tr><th>应用组</th><th>PID</th><th>名称</th><th>可执行路径</th>"
        "<th>用户</th><th>启动时间</th><th>分类</th><th>决定/原因</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def _result_html(plan: ProcessActionPlan, result: ProcessActionToolResult) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{item.pid}</td><td>{item.state.value}</td>"
        f"<td>{item.windows_notified}</td><td>{escape(item.message)}</td>"
        "</tr>"
        for item in result.members
    )
    return (
        f"<h3>验证结果：{plan.action.value}</h3>"
        f"<p>全部原身份已退出：{'是' if result.all_exited else '否'}。"
        "结果只针对确认时绑定的 PID + 启动时间 + 路径 + 用户身份。</p>"
        "<table border='1' cellspacing='0' cellpadding='4'>"
        "<tr><th>PID</th><th>状态</th><th>通知窗口数</th><th>说明</th></tr>"
        f"{rows}</table>"
        "<p>回滚：NONE。重新启动应用只能恢复进程，不等于恢复未保存数据。</p>"
    )


def _force_is_valid_alternative(
    plan: ProcessActionPlan,
    preview: ProcessActionPreview,
) -> bool:
    if plan.action is not ProcessActionType.REQUEST_GRACEFUL_EXIT:
        return False
    allowed_reason = ProcessActionErrorCode.UNSUPPORTED_GRACEFUL_EXIT
    return bool(preview.assessments) and all(
        item.decision is ProcessSafetyDecision.BLOCK and item.reason_codes == (allowed_reason,)
        for item in preview.assessments
    )
