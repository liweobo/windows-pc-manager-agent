"""Non-technical two-confirmation UI for one controlled winget removal."""

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

from pc_manager_agent.app.runtime import ApplicationRuntime, WingetUninstallServices
from pc_manager_agent.confirmation.winget_uninstall import WingetUninstallConfirmation
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.domain.winget_uninstall import (
    WingetUninstallExecutionReport,
    WingetUninstallPlan,
    WingetUninstallPreview,
)
from pc_manager_agent.ui.winget_uninstall_workers import (
    WingetRuntimePrepareWorker,
    WingetUninstallExecuteWorker,
    WingetUninstallPrepareWorker,
    require_prepared_winget_uninstall,
    require_runtime_winget_confirmation,
    require_winget_uninstall_report,
)


class WingetUninstallDialog(QDialog):
    """Keep cancellation as default while exposing one exact package transaction."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        user_goal: str,
        *,
        query: SoftwareTargetQuery,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._user_goal = user_goal
        self._query = query
        self._services: WingetUninstallServices | None = None
        self._plan: WingetUninstallPlan | None = None
        self._preview: WingetUninstallPreview | None = None
        self._confirmation: WingetUninstallConfirmation | None = None
        self._worker: object | None = None
        self._stage = "PREPARING"
        self.setWindowTitle("winget 安全卸载 — 双重确认")
        self.resize(780, 620)
        self.setModal(False)
        self._build_ui()
        self._start_prepare()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.risk_label = QLabel(
            "R2：仅支持一个当前用户、官方源、高可信映射的软件包；无法自动撤销。"
        )
        self.risk_label.setStyleSheet("font-weight: 700; color: #a33a00;")
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        buttons = QHBoxLayout()
        self.primary_button = QPushButton("正在验证")
        self.cancel_button = QPushButton("取消（默认）")
        self.primary_button.setEnabled(False)
        self.cancel_button.setDefault(True)
        self.cancel_button.setAutoDefault(True)
        self.primary_button.setAutoDefault(False)
        self.primary_button.clicked.connect(self._primary_clicked)
        self.cancel_button.clicked.connect(self._cancel_clicked)
        buttons.addStretch(1)
        buttons.addWidget(self.primary_button)
        buttons.addWidget(self.cancel_button)
        layout.addWidget(self.risk_label)
        layout.addWidget(self.details, 1)
        layout.addWidget(self.progress)
        layout.addLayout(buttons)

    def _start_prepare(self) -> None:
        self._set_busy("正在核对 Package ID、官方源、软件映射和 winget 身份。尚未执行卸载。")
        worker = WingetUninstallPrepareWorker(self._runtime, self._user_goal, self._query)
        worker.signals.completed.connect(self._prepared)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _prepared(self, value: object) -> None:
        self._worker = None
        try:
            bundled = require_prepared_winget_uninstall(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        prepared = bundled.prepared
        if prepared.plan is None or prepared.preview is None or prepared.plan_confirmation is None:
            self._failed("Package 目标不唯一或已变化；请刷新软件清单后重新选择。")
            return
        self._services = bundled.services
        self._plan = prepared.plan
        self._preview = prepared.preview
        self._confirmation = prepared.plan_confirmation
        self._stage = "PLAN_CONFIRMATION"
        self._show_preview(prepared.preview, immediate=False)

    @Slot()
    def _primary_clicked(self) -> None:
        if self._stage == "PLAN_CONFIRMATION":
            self._approve_plan()
        elif self._stage == "RUNTIME_CONFIRMATION":
            self._approve_runtime()
        elif self._stage in {"DONE", "FAILED"}:
            self.accept()

    def _approve_plan(self) -> None:
        services, plan, preview, confirmation = self._required_state()
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
        worker = WingetRuntimePrepareWorker(services, confirmation.confirmation_id, plan)
        worker.signals.completed.connect(self._runtime_prepared)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        self._stage = "REVALIDATING"
        self._set_busy("正在重新验证全部证据；变化会使第一次确认自动失效。")
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _runtime_prepared(self, value: object) -> None:
        self._worker = None
        try:
            prepared = require_runtime_winget_confirmation(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._preview = prepared.preview
        self._confirmation = prepared.confirmation
        self._stage = "RUNTIME_CONFIRMATION"
        self._show_preview(prepared.preview, immediate=True)

    def _approve_runtime(self) -> None:
        services, plan, preview, confirmation = self._required_state()
        if services is None or plan is None or preview is None or confirmation is None:
            return
        try:
            services.service.resolve_runtime_confirmation(
                confirmation.confirmation_id,
                True,
                plan,
                preview,
            )
        except Exception as exc:
            self._failed(f"{type(exc).__name__}: {exc}")
            return
        worker = WingetUninstallExecuteWorker(
            services,
            confirmation.confirmation_id,
            plan,
            preview,
        )
        worker.signals.completed.connect(self._completed)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        self._stage = "EXECUTING"
        self._set_busy("winget 已启动。取消只会停止监控，不会强制结束 winget 或底层安装器。")
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _completed(self, value: object) -> None:
        self._worker = None
        try:
            report = require_winget_uninstall_report(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._stage = "DONE"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.details.setHtml(_report_html(report))
        self.risk_label.setText(
            "执行已结束；最终结论来自 Package 与软件清单双重刷新，不来自退出码。"
        )
        self.primary_button.setText("关闭")
        self.primary_button.setEnabled(True)

    def _show_preview(self, preview: WingetUninstallPreview, *, immediate: bool) -> None:
        heading = "即时确认：即将执行" if immediate else "计划确认：尚未执行"
        warnings = (*preview.preflight.warnings, *preview.preflight.blockers)
        warning_html = "".join(f"<li>{escape(item)}</li>" for item in warnings)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.details.setHtml(
            f"<h3>{heading}</h3>"
            f"<p><b>软件：</b>{escape(preview.software.display_name)}</p>"
            f"<p><b>版本：</b>{escape(preview.package.installed_version)}</p>"
            f"<p><b>Package ID：</b>{escape(preview.package.package_id)}</p>"
            "<p><b>源：</b>winget（官方社区源）；<b>范围：</b>当前用户；"
            f"<b>风险：</b>{escape(preview.execution_assessment.risk_level.value)}</p>"
            "<p><b>执行方式：</b>固定参数、无 Shell、不提权、不自动重启。</p>"
            "<p><b>回滚：</b>NONE。重新安装只是手动恢复，不是 Undo。</p>"
            + (f"<ul>{warning_html}</ul>" if warning_html else "")
        )
        self.primary_button.setText("确认计划" if not immediate else "再次确认并卸载")
        self.primary_button.setEnabled(preview.executable)

    @Slot(str)
    def _failed(self, message: str) -> None:
        self._worker = None
        self._stage = "FAILED"
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.risk_label.setText("操作已停止；未自动重试。")
        self.details.setHtml(f"<h3>无法继续</h3><p>{escape(message)}</p>")
        self.primary_button.setText("关闭")
        self.primary_button.setEnabled(True)

    @Slot()
    def _cancel_clicked(self) -> None:
        worker = self._worker
        cancel = getattr(worker, "cancel", None)
        if callable(cancel):
            cancel()
        if self._stage == "EXECUTING":
            self.risk_label.setText("已请求停止监控；不会强制结束 winget 或安装器。")
            self.cancel_button.setEnabled(False)
            return
        self.reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Route window-close through the same non-terminating cancellation semantics."""
        self._cancel_clicked()
        event.accept()

    def _set_busy(self, message: str) -> None:
        self.progress.setRange(0, 0)
        self.primary_button.setEnabled(False)
        self.details.setHtml(f"<p>{escape(message)}</p>")

    def _required_state(
        self,
    ) -> tuple[
        WingetUninstallServices | None,
        WingetUninstallPlan | None,
        WingetUninstallPreview | None,
        WingetUninstallConfirmation | None,
    ]:
        return self._services, self._plan, self._preview, self._confirmation


def _report_html(report: WingetUninstallExecutionReport) -> str:
    evidence = "".join(f"<li>{escape(item)}</li>" for item in report.verification.evidence)
    warnings = "".join(f"<li>{escape(item)}</li>" for item in report.verification.warnings)
    return (
        "<h3>winget 执行与验证结果</h3>"
        f"<p><b>目标：</b>{escape(report.target_summary)}</p>"
        f"<p><b>进程结果：</b>{escape(report.process.category.value)}；"
        f"退出码：{escape(str(report.process.exit_code))}</p>"
        f"<p><b>双重验证：</b>{escape(report.verification.state.value)}</p>"
        f"<ul>{evidence}{warnings}</ul>"
        f"<p><b>恢复说明：</b>{escape(report.recovery_guidance)}</p>"
    )
