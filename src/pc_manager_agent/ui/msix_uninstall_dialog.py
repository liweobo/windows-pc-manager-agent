"""Non-technical two-confirmation UI for one controlled MSIX removal."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QThreadPool, Slot
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

from pc_manager_agent.app.runtime import ApplicationRuntime, MsixUninstallServices
from pc_manager_agent.confirmation.msix_uninstall import MsixUninstallConfirmation
from pc_manager_agent.domain.msix_uninstall import (
    MsixTargetQuery,
    MsixUninstallPlan,
    MsixUninstallPreview,
    MsixUninstallResult,
)
from pc_manager_agent.ui.msix_uninstall_workers import (
    MsixExecuteWorker,
    MsixPrepareWorker,
    MsixRuntimePrepareWorker,
    require_msix_result,
    require_msix_runtime,
    require_prepared_msix,
)


class MsixUninstallDialog(QDialog):
    """Keep cancellation as default and show exact Windows data impact twice."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        user_goal: str,
        *,
        query: MsixTargetQuery,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._goal = user_goal
        self._query = query
        self._services: MsixUninstallServices | None = None
        self._plan: MsixUninstallPlan | None = None
        self._preview: MsixUninstallPreview | None = None
        self._confirmation: MsixUninstallConfirmation | None = None
        self._worker: object | None = None
        self._stage = "PREPARING"
        self.setWindowTitle("MSIX / Store App 安全卸载 — 双重确认")
        self.resize(820, 650)
        self._build_ui()
        self._start_prepare()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.risk = QLabel("R2：仅当前用户普通 App；Windows 数据影响不可完整撤销。")
        self.risk.setStyleSheet("font-weight: 700; color: #a33a00;")
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        buttons = QHBoxLayout()
        self.primary = QPushButton("正在验证")
        self.cancel = QPushButton("取消（默认）")
        self.primary.setEnabled(False)
        self.cancel.setDefault(True)
        self.primary.clicked.connect(self._primary_clicked)
        self.cancel.clicked.connect(self._cancel_clicked)
        buttons.addStretch(1)
        buttons.addWidget(self.primary)
        buttons.addWidget(self.cancel)
        layout.addWidget(self.risk)
        layout.addWidget(self.details, 1)
        layout.addWidget(self.progress)
        layout.addLayout(buttons)

    def _start_prepare(self) -> None:
        worker = MsixPrepareWorker(self._runtime, self._goal, self._query)
        worker.signals.completed.connect(self._prepared)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _prepared(self, value: object) -> None:
        self._worker = None
        try:
            bundle = require_prepared_msix(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        prepared = bundle.prepared
        if prepared.plan is None or prepared.preview is None or prepared.plan_confirmation is None:
            self._failed("目标不唯一；请重新选择一个精确 Package。")
            return
        self._services = bundle.services
        self._plan = prepared.plan
        self._preview = prepared.preview
        self._confirmation = prepared.plan_confirmation
        self._stage = "PLAN"
        self._show_preview(prepared.preview, immediate=False)

    @Slot()
    def _primary_clicked(self) -> None:
        state = self._required()
        if self._stage == "PLAN" and state:
            services, plan, preview, confirmation = state
            try:
                services.service.resolve_confirmation(
                    confirmation.confirmation_id, True, plan, preview
                )
            except Exception as exc:
                self._failed(f"{type(exc).__name__}: {exc}")
                return
            runtime_worker = MsixRuntimePrepareWorker(
                services, confirmation.confirmation_id, plan, preview
            )
            runtime_worker.signals.completed.connect(self._runtime_prepared)
            runtime_worker.signals.failed.connect(self._failed)
            self._worker = runtime_worker
            self._stage = "REVALIDATING"
            self._busy("正在重新验证 Package 版本、依赖和运行状态。")
            QThreadPool.globalInstance().start(runtime_worker)
        elif self._stage == "RUNTIME" and state:
            services, plan, preview, confirmation = state
            try:
                services.service.resolve_confirmation(
                    confirmation.confirmation_id, True, plan, preview
                )
            except Exception as exc:
                self._failed(f"{type(exc).__name__}: {exc}")
                return
            execute_worker = MsixExecuteWorker(
                services, confirmation.confirmation_id, plan, preview
            )
            execute_worker.signals.completed.connect(self._completed)
            execute_worker.signals.failed.connect(self._failed)
            self._worker = execute_worker
            self._stage = "EXECUTING"
            self._busy("Windows 正在处理卸载；Agent 不终止进程、不停止服务、不自动重试。")
            QThreadPool.globalInstance().start(execute_worker)
        elif self._stage in {"DONE", "FAILED"}:
            self.accept()

    @Slot(object)
    def _runtime_prepared(self, value: object) -> None:
        self._worker = None
        try:
            prepared = require_msix_runtime(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._preview = prepared.preview
        self._confirmation = prepared.confirmation
        self._stage = "RUNTIME"
        self._show_preview(prepared.preview, immediate=True)

    @Slot(object)
    def _completed(self, value: object) -> None:
        self._worker = None
        try:
            result = require_msix_result(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._stage = "DONE"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.details.setHtml(_result_html(result))
        self.risk.setText("执行结束；最终结论来自卸载后的当前用户 Package 清单。")
        self.primary.setText("关闭")
        self.primary.setEnabled(True)

    def _show_preview(self, preview: MsixUninstallPreview, *, immediate: bool) -> None:
        identity = preview.package.identity
        heading = "即时确认：即将请求 Windows 卸载" if immediate else "计划确认：尚未卸载"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.details.setHtml(
            f"<h3>{heading}</h3>"
            f"<p><b>应用：</b>{escape(preview.package.display_name)}</p>"
            f"<p><b>Package Full Name：</b>{escape(identity.instance.full_name)}</p>"
            f"<p><b>Family：</b>{escape(identity.family.family_name)}</p>"
            f"<p><b>版本 / 架构：</b>{escape(identity.instance.version)} / "
            f"{escape(identity.instance.architecture)}</p>"
            "<p><b>范围：</b>仅当前用户；<b>类型：</b>普通 User MSIX App；<b>风险：</b>R2</p>"
            "<p><b>数据影响：</b>请求保留 Roamable 数据；Windows 可能移除 Package-managed "
            "LocalState，并可能移除无人依赖的依赖包。本流程已在发现依赖风险时阻止执行。</p>"
            "<p><b>Agent 保证：</b>不额外删除 AppData/用户文件，不运行 PowerShell，不提权，"
            "不结束进程，不停止服务。</p>"
            "<p><b>回滚：</b>NONE。重新安装是手动恢复，不是 Undo。</p>"
        )
        self.primary.setText("确认计划" if not immediate else "再次确认并卸载")
        self.primary.setEnabled(preview.executable)

    def _busy(self, message: str) -> None:
        self.details.setHtml(f"<p>{escape(message)}</p>")
        self.progress.setRange(0, 0)
        self.primary.setEnabled(False)

    @Slot(str)
    def _failed(self, message: str) -> None:
        self._worker = None
        self._stage = "FAILED"
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.details.setHtml(f"<h3>已安全停止</h3><p>{escape(message)}</p>")
        self.primary.setText("关闭")
        self.primary.setEnabled(True)

    @Slot()
    def _cancel_clicked(self) -> None:
        worker = self._worker
        cancel = getattr(worker, "cancel", None)
        if callable(cancel):
            cancel()
        self.reject()

    def _required(
        self,
    ) -> (
        tuple[
            MsixUninstallServices,
            MsixUninstallPlan,
            MsixUninstallPreview,
            MsixUninstallConfirmation,
        ]
        | None
    ):
        if not all((self._services, self._plan, self._preview, self._confirmation)):
            self._failed("内部状态不完整，操作未执行。")
            return None
        services = self._services
        plan = self._plan
        preview = self._preview
        confirmation = self._confirmation
        if services is None or plan is None or preview is None or confirmation is None:
            return None
        return services, plan, preview, confirmation

    def closeEvent(self, event: QCloseEvent) -> None:
        """Request cooperative cancellation before closing the dialog."""
        self._cancel_clicked()
        event.accept()


def _result_html(result: MsixUninstallResult) -> str:
    """Render deployment and verification separately to avoid false success claims."""
    return (
        "<h3>MSIX 卸载结果</h3>"
        f"<p><b>Windows 返回：</b>{escape(result.deployment.category.value)}</p>"
        f"<p><b>清单验证：</b>{escape(result.verification.state.value)}</p>"
        f"<p>{escape(result.verification.reason)}</p>"
        "<p>Agent 未枚举或额外删除用户数据。回滚等级：NONE。</p>"
    )
