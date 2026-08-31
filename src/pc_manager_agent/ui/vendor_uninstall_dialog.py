"""Object-specific two-confirmation UI for one controlled Vendor uninstaller."""

from __future__ import annotations

from html import escape

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

from pc_manager_agent.app.runtime import ApplicationRuntime, VendorUninstallServices
from pc_manager_agent.confirmation.vendor_uninstall import VendorUninstallConfirmation
from pc_manager_agent.domain.optimization_receipts import OptimizationReceiptKind
from pc_manager_agent.domain.software_uninstall_analysis import (
    NormalizedInstalledSoftware,
    SoftwareTargetQuery,
)
from pc_manager_agent.domain.vendor_uninstall import (
    VendorUninstallExecutionReport,
    VendorUninstallPlan,
    VendorUninstallPreview,
    VendorVerificationState,
)
from pc_manager_agent.ui.domain_review_events import ObservedDomainDialog
from pc_manager_agent.ui.residual_analysis_dialog import ResidualAnalysisDialog
from pc_manager_agent.ui.vendor_uninstall_workers import (
    VendorRuntimePrepareWorker,
    VendorUninstallExecuteWorker,
    VendorUninstallPrepareWorker,
    require_prepared_vendor_uninstall,
    require_runtime_vendor_confirmation,
    require_vendor_uninstall_report,
)


class VendorUninstallDialog(ObservedDomainDialog):
    """Keep cancellation as default while exposing one trusted Vendor transaction."""

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
        self._services: VendorUninstallServices | None = None
        self._plan: VendorUninstallPlan | None = None
        self._preview: VendorUninstallPreview | None = None
        self._plan_confirmation: VendorUninstallConfirmation | None = None
        self._runtime_confirmation: VendorUninstallConfirmation | None = None
        self._worker: object | None = None
        self._cancellable_worker: object | None = None
        self._stage = "PREPARING"
        self._close_when_safe = False
        self._residual_dialog: ResidualAnalysisDialog | None = None
        self.setWindowTitle("受控厂商卸载程序 — Stage 4D2B")
        self.resize(960, 760)
        self.setModal(False)
        self._build_ui()
        self._start_prepare(query)

    def _build_ui(self) -> None:
        """Create the finite state display with Cancel as the default action."""
        layout = QVBoxLayout(self)
        self.risk_label = QLabel("正在只读验证身份。默认不执行；厂商卸载不可自动回滚。")
        self.risk_label.setStyleSheet("font-weight: 700; color: #9c3d10;")
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        self.candidates = QTableWidget(0, 6)
        self.candidates.setHorizontalHeaderLabels(
            ("名称", "版本", "发布者", "范围", "架构", "来源")
        )
        self.candidates.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.candidates.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.candidates.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.candidates.setVisible(False)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        buttons = QHBoxLayout()
        self.primary_button = QPushButton("等待安全审查")
        self.cancel_button = QPushButton("取消（默认）")
        self.residual_button = QPushButton("分析可能残留")
        self.residual_button.setVisible(False)
        self.primary_button.setEnabled(False)
        self.primary_button.setAutoDefault(False)
        self.cancel_button.setDefault(True)
        self.cancel_button.setAutoDefault(True)
        self.primary_button.clicked.connect(self._primary_clicked)
        self.cancel_button.clicked.connect(self._cancel_clicked)
        self.residual_button.clicked.connect(self._open_residual_analysis)
        buttons.addStretch(1)
        buttons.addWidget(self.residual_button)
        buttons.addWidget(self.primary_button)
        buttons.addWidget(self.cancel_button)
        layout.addWidget(self.risk_label)
        layout.addWidget(self.details, 2)
        layout.addWidget(self.candidates, 1)
        layout.addWidget(self.progress)
        layout.addLayout(buttons)

    def _start_prepare(self, query: SoftwareTargetQuery) -> None:
        self._set_busy("正在刷新软件、签名、卸载器身份、参数、进程和服务；尚未启动。")
        worker = VendorUninstallPrepareWorker(self._runtime, self._user_goal, query)
        worker.signals.completed.connect(self._prepared)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        self._cancellable_worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _prepared(self, value: object) -> None:
        self._worker = None
        self._cancellable_worker = None
        try:
            bundled = require_prepared_vendor_uninstall(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._services = bundled.services
        prepared = bundled.prepared
        if prepared.plan is None or prepared.preview is None:
            self._show_candidates(prepared.resolution.candidates, prepared.resolution.reason)
            return
        if prepared.plan_confirmation is None or prepared.review is None:
            self._failed("安全审查未创建可用的计划确认；没有启动卸载。")
            return
        self._plan = prepared.plan
        self.publish_domain_preview(OptimizationReceiptKind.VENDOR, prepared.plan.transaction_id)
        self._preview = prepared.preview
        self._plan_confirmation = prepared.plan_confirmation
        self._show_preview(prepared.preview, immediate=False)
        self._stage = "PLAN_CONFIRMATION"
        self.primary_button.setText("确认计划并重新验证")
        self.primary_button.setEnabled(True)
        self.cancel_button.setEnabled(True)

    def _show_candidates(
        self,
        candidates: tuple[NormalizedInstalledSoftware, ...],
        reason: str,
    ) -> None:
        """Require one explicit candidate when a name is not unique."""
        self._stage = "CANDIDATE_SELECTION"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.details.setHtml(
            "<h3>必须明确选择一个软件</h3>"
            f"<p>{escape(reason)}</p>"
            "<p>不会按相似名称自动选择，也不会执行任何原始卸载命令。</p>"
        )
        self.candidates.setRowCount(len(candidates))
        for row, item in enumerate(candidates):
            values = (
                item.display_name,
                item.display_version or "",
                item.publisher or "",
                item.scope.value,
                item.architecture.value,
                item.source.value,
            )
            for column, text in enumerate(values):
                cell = QTableWidgetItem(text)
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, item.identity.canonical_digest())
                self.candidates.setItem(row, column, cell)
        self.candidates.setVisible(True)
        if candidates:
            self.candidates.selectRow(0)
            self.primary_button.setText("选择此目标并重新审查")
            self.primary_button.setEnabled(True)
        else:
            self.risk_label.setText("没有找到匹配软件；未执行任何操作。")

    @Slot()
    def _primary_clicked(self) -> None:
        if self._stage == "CANDIDATE_SELECTION":
            self._select_candidate()
        elif self._stage == "PLAN_CONFIRMATION":
            self._approve_plan()
        elif self._stage == "RUNTIME_CONFIRMATION":
            self._approve_runtime_and_execute()
        elif self._stage in {"COMPLETED", "FAILED"}:
            self.accept()

    def _select_candidate(self) -> None:
        """Restart preparation using only the explicitly selected identity digest."""
        row = self.candidates.currentRow()
        item = self.candidates.item(row, 0) if row >= 0 else None
        digest = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(digest, str):
            self.risk_label.setText("请先选择一个具体软件。")
            return
        self.candidates.setVisible(False)
        self._start_prepare(SoftwareTargetQuery(identity_digest=digest))

    def _approve_plan(self) -> None:
        services = self._services
        plan = self._plan
        preview = self._preview
        confirmation = self._plan_confirmation
        if services is None or plan is None or preview is None or confirmation is None:
            self._failed("计划确认状态不完整；没有启动卸载。")
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
        worker = VendorRuntimePrepareWorker(services, confirmation.confirmation_id, plan)
        worker.signals.completed.connect(self._runtime_prepared)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        self._cancellable_worker = worker
        self._stage = "REVALIDATING"
        self._set_busy("计划已确认。正在重新验证软件、卸载器文件、签名、参数和运行状态。")
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _runtime_prepared(self, value: object) -> None:
        self._worker = None
        self._cancellable_worker = None
        try:
            prepared = require_runtime_vendor_confirmation(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._preview = prepared.preview
        self._runtime_confirmation = prepared.confirmation
        self._stage = "RUNTIME_CONFIRMATION"
        self._show_preview(prepared.preview, immediate=True)
        self.primary_button.setText("确认启动这个厂商卸载程序")
        self.primary_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        if prepared.preview.execution_assessment.risk_level.value == "R2_HIGH_IMPACT":
            self.primary_button.setStyleSheet("font-weight: 700; color: #a00000;")

    def _approve_runtime_and_execute(self) -> None:
        services = self._services
        plan = self._plan
        preview = self._preview
        confirmation = self._runtime_confirmation
        if services is None or plan is None or preview is None or confirmation is None:
            self._failed("即时确认状态不完整；没有启动卸载。")
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
        worker = VendorUninstallExecuteWorker(
            services,
            confirmation.confirmation_id,
            plan,
            preview,
        )
        worker.signals.completed.connect(self._completed)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        self._cancellable_worker = worker
        self._stage = "EXECUTING"
        self._set_busy("厂商卸载界面已获单次启动授权。请自行处理其窗口；应用不会点击、提权或强杀。")
        self.cancel_button.setText("停止监控（不会终止卸载器）")
        self.cancel_button.setEnabled(True)
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _completed(self, value: object) -> None:
        self._worker = None
        self._cancellable_worker = None
        try:
            report = require_vendor_uninstall_report(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._stage = "COMPLETED"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.risk_label.setText("进程观察已结束；最终结论来自刷新后的本地软件清单。")
        self.details.setHtml(_report_html(report))
        self.primary_button.setText("关闭")
        self.primary_button.setEnabled(True)
        self.cancel_button.setText("关闭")
        self.residual_button.setVisible(
            report.verification.state
            in {
                VendorVerificationState.VERIFIED_REMOVED,
                VendorVerificationState.COMPLETED_UNVERIFIED,
                VendorVerificationState.REMOVED_WITH_UNEXPECTED_PROCESS_RESULT,
            }
        )

    @Slot()
    def _open_residual_analysis(self) -> None:
        """Open a new read-only plan without reusing uninstall authorization."""
        plan = self._plan
        if plan is None:
            return
        dialog = ResidualAnalysisDialog(self._runtime, plan.transaction_id, parent=self)
        self._residual_dialog = dialog
        dialog.show()
        self.cancel_button.setEnabled(True)

    def _show_preview(self, preview: VendorUninstallPreview, *, immediate: bool) -> None:
        """Render trust and impact facts without a dangerous full command line."""
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.candidates.setVisible(False)
        self.details.setHtml(_preview_html(preview, immediate=immediate))
        self.risk_label.setText(
            "即时确认：软件、文件身份、参数摘要、风险和 Preview 均已绑定。"
            if immediate
            else "计划确认：执行前会再次读取文件、签名、参数、进程和服务。"
        )

    @Slot()
    def _cancel_clicked(self) -> None:
        worker = self._cancellable_worker
        if worker is not None and hasattr(worker, "cancel"):
            worker.cancel()
            self._close_when_safe = True
            self.cancel_button.setEnabled(False)
            self.risk_label.setText("已请求停止。已启动的厂商进程不会被终止或自动重试。")
            return
        if self._stage == "PLAN_CONFIRMATION":
            self._reject_plan_and_close()
        elif self._stage == "RUNTIME_CONFIRMATION":
            self._reject_runtime_and_close()
        else:
            self.reject()

    def _reject_plan_and_close(self) -> None:
        if self._services and self._plan and self._preview and self._plan_confirmation:
            try:
                self._services.service.resolve_plan_confirmation(
                    self._plan_confirmation.confirmation_id,
                    False,
                    self._plan,
                    self._preview,
                )
            finally:
                self.reject()
            return
        self.reject()

    def _reject_runtime_and_close(self) -> None:
        if self._services and self._plan and self._preview and self._runtime_confirmation:
            try:
                self._services.service.resolve_runtime_confirmation(
                    self._runtime_confirmation.confirmation_id,
                    False,
                    self._plan,
                    self._preview,
                )
            finally:
                self.reject()
            return
        self.reject()

    @Slot(str)
    def _failed(self, message: str) -> None:
        self._worker = None
        self._cancellable_worker = None
        self._stage = "FAILED"
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.risk_label.setText("流程已停止。没有重试、提权、关进程、停服务或删除残留。")
        self.details.setPlainText(message)
        self.primary_button.setText("关闭")
        self.primary_button.setEnabled(True)
        self.cancel_button.setText("关闭")
        self.cancel_button.setEnabled(True)

    def _set_busy(self, text: str) -> None:
        self.risk_label.setText(text)
        self.progress.setRange(0, 0)
        self.primary_button.setEnabled(False)

    def shutdown(self) -> None:
        """Request cancellation without terminating any already launched process."""
        worker = self._cancellable_worker
        if worker is not None and hasattr(worker, "cancel"):
            worker.cancel()
        self._close_when_safe = True

    def closeEvent(self, event: QCloseEvent) -> None:
        """Reject pending approvals and keep active monitoring owned by its worker."""
        if self._worker is not None:
            self.shutdown()
            event.ignore()
            return
        super().closeEvent(event)


def _preview_html(preview: VendorUninstallPreview, *, immediate: bool) -> str:
    """Render safe trust fields and never render full executable path or argv."""
    identity = preview.vendor_identity
    signature = identity.executable.authenticode
    signer = signature.signer_organization or signature.signer_subject or "未知"
    warnings = (*preview.preflight.warnings, *preview.execution_assessment.reasons)
    warning_html = "".join(f"<li>{escape(item)}</li>" for item in warnings)
    mode = "执行前即时确认" if immediate else "计划确认"
    return (
        f"<h2>{escape(preview.target.display_name)}</h2>"
        f"<p><b>{mode}</b>；风险 {escape(preview.execution_assessment.risk_level.value)}；"
        "回滚 NONE，只能以后手动重新安装，且重新安装不是 Undo。</p>"
        "<h3>可信验证</h3>"
        f"<p>签名：{escape(signature.status.value)}；签名者：{escape(signer)}；"
        f"发布者匹配：{escape(identity.executable.publisher_match.value)}；"
        f"安装目录关系：{escape(identity.executable.install_location_relation.value)}。</p>"
        f"<p>文件身份摘要：{escape(identity.executable.file_identity.canonical_digest())}；"
        f"SHA-256：{escape(identity.executable.file_identity.sha256)}。</p>"
        "<h3>参数与执行边界</h3>"
        f"<p>参数数量：{identity.argument_assessment.argument_count}；"
        f"策略：{escape(identity.argument_assessment.decision.value)}；不会显示或修改完整命令。"
        "使用绝对路径、参数数组、shell=False、固定工作目录和脱敏环境。</p>"
        "<p>厂商界面会显示；应用不会自动点击、请求 UAC、停止服务、结束进程或重启电脑。</p>"
        f"<p>相关进程 {len(preview.preflight.related_processes)} 个；"
        f"相关服务 {len(preview.preflight.related_services)} 个。</p>"
        f"<ul>{warning_html}</ul>"
    )


def _report_html(report: VendorUninstallExecutionReport) -> str:
    """Render process and refreshed inventory evidence as separate conclusions."""
    evidence = "".join(f"<li>{escape(item)}</li>" for item in report.verification.evidence)
    warnings = "".join(f"<li>{escape(item)}</li>" for item in report.verification.warnings)
    return (
        f"<h2>{escape(report.target_summary)}</h2>"
        f"<p>进程结果：{escape(report.process.category.value)}；"
        f"进程 ID：{escape(str(report.process.process_id))}；"
        f"退出码：{escape(str(report.process.exit_code))}；"
        f"跟踪子进程：{report.process.tracked_child_count}。</p>"
        f"<p><b>刷新验证：{escape(report.verification.state.value)}</b>；"
        f"原身份仍存在：{escape(str(report.verification.original_identity_present))}。</p>"
        f"<ul>{evidence}{warnings}</ul>"
        f"<p>已知安装目录仍存在：{escape(str(report.residual.install_location_present))}；"
        "未删除任何残留或用户数据。</p>"
        f"<p>{escape(report.recovery_guidance)}</p>"
    )
