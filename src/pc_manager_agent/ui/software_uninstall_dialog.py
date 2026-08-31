"""Object-specific two-confirmation UI for one controlled MSI uninstall."""

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

from pc_manager_agent.app.runtime import ApplicationRuntime, MsiUninstallServices
from pc_manager_agent.confirmation.software_uninstall_execution import (
    MsiUninstallConfirmation,
)
from pc_manager_agent.domain.optimization_receipts import OptimizationReceiptKind
from pc_manager_agent.domain.software_uninstall_analysis import (
    NormalizedInstalledSoftware,
    SoftwareTargetQuery,
)
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiUninstallExecutionReport,
    MsiUninstallPlan,
    MsiUninstallPreview,
    MsiVerificationState,
)
from pc_manager_agent.ui.domain_review_events import ObservedDomainDialog
from pc_manager_agent.ui.residual_analysis_dialog import ResidualAnalysisDialog
from pc_manager_agent.ui.software_uninstall_workers import (
    MsiRuntimePrepareWorker,
    MsiUninstallExecuteWorker,
    MsiUninstallPrepareWorker,
    require_msi_uninstall_report,
    require_prepared_msi_uninstall,
    require_runtime_msi_confirmation,
)


class SoftwareUninstallDialog(ObservedDomainDialog):
    """Keep cancellation as default while exposing only one validated MSI transaction."""

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
        self._services: MsiUninstallServices | None = None
        self._plan: MsiUninstallPlan | None = None
        self._preview: MsiUninstallPreview | None = None
        self._plan_confirmation: MsiUninstallConfirmation | None = None
        self._runtime_confirmation: MsiUninstallConfirmation | None = None
        self._worker: object | None = None
        self._cancellable_worker: object | None = None
        self._stage = "PREPARING"
        self._close_when_safe = False
        self._residual_dialog: ResidualAnalysisDialog | None = None
        self.setWindowTitle("受控 MSI 软件卸载 — Stage 4D2A")
        self.resize(960, 760)
        self.setModal(False)
        self._build_ui()
        self._start_prepare(query)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.risk_label = QLabel("正在只读验证身份。默认不执行；软件卸载不可自动回滚。")
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
        self._set_busy("正在刷新软件清单、MSI 注册、进程和服务状态；尚未启动卸载。")
        worker = MsiUninstallPrepareWorker(self._runtime, self._user_goal, query)
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
            value_with_services = require_prepared_msi_uninstall(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._services = value_with_services.services
        prepared = value_with_services.prepared
        if prepared.plan is None or prepared.preview is None:
            self._show_candidates(prepared.resolution.candidates, prepared.resolution.reason)
            return
        if prepared.plan_confirmation is None or prepared.review is None:
            self._failed("安全审查未创建可用的计划确认；没有启动卸载。")
            return
        self._plan = prepared.plan
        self.publish_domain_preview(OptimizationReceiptKind.MSI, prepared.plan.transaction_id)
        self._preview = prepared.preview
        self._plan_confirmation = prepared.plan_confirmation
        self._show_preview(prepared.preview, immediate=False)
        self._stage = "PLAN_CONFIRMATION"
        self.primary_button.setText("确认计划并重新验证")
        self.primary_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        if self._close_when_safe:
            self._reject_plan_and_close()

    def _show_candidates(
        self,
        candidates: tuple[NormalizedInstalledSoftware, ...],
        reason: str,
    ) -> None:
        self._stage = "CANDIDATE_SELECTION"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.details.setHtml(
            "<h3>必须明确选择一个软件</h3>"
            f"<p>{escape(reason)}</p>"
            "<p>不会按显示名称自动选择，也不会让模型提供 ProductCode。</p>"
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
            self.primary_button.setEnabled(False)

    @Slot()
    def _primary_clicked(self) -> None:
        if self._stage == "CANDIDATE_SELECTION":
            self._select_candidate()
        elif self._stage == "PLAN_CONFIRMATION":
            self._approve_plan()
        elif self._stage == "RUNTIME_CONFIRMATION":
            self._approve_runtime_and_execute()
        elif self._stage in {"COMPLETED", "FAILED", "BLOCKED"}:
            self.accept()

    def _select_candidate(self) -> None:
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
            self._failed("内部计划确认状态不完整；没有启动卸载。")
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
        worker = MsiRuntimePrepareWorker(
            services,
            confirmation.confirmation_id,
            plan,
        )
        worker.signals.completed.connect(self._runtime_prepared)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        self._cancellable_worker = worker
        self._stage = "REVALIDATING"
        self._set_busy("计划已确认。正在重新验证身份、ProductCode、策略和运行状态。")
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _runtime_prepared(self, value: object) -> None:
        self._worker = None
        self._cancellable_worker = None
        try:
            prepared = require_runtime_msi_confirmation(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._preview = prepared.preview
        self._runtime_confirmation = prepared.confirmation
        self._stage = "RUNTIME_CONFIRMATION"
        self._show_preview(prepared.preview, immediate=True)
        self.primary_button.setText("确认卸载这个软件")
        self.primary_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setDefault(True)
        if prepared.preview.execution_assessment.risk_level.value == "R2_HIGH_IMPACT":
            self.primary_button.setStyleSheet("font-weight: 700; color: #a00000;")
        if self._close_when_safe:
            self._reject_runtime_and_close()

    def _approve_runtime_and_execute(self) -> None:
        services = self._services
        plan = self._plan
        preview = self._preview
        confirmation = self._runtime_confirmation
        if services is None or plan is None or preview is None or confirmation is None:
            self._failed("内部即时确认状态不完整；没有启动卸载。")
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
        worker = MsiUninstallExecuteWorker(
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
        self._set_busy(
            "Windows Installer 已获单次授权。取消不会强制终止已启动的安装器，请处理其窗口。"
        )
        self.cancel_button.setText("请求取消（不会强杀 MSI）")
        self.cancel_button.setEnabled(True)
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _completed(self, value: object) -> None:
        self._worker = None
        self._cancellable_worker = None
        try:
            report = require_msi_uninstall_report(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._stage = "COMPLETED"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.risk_label.setText("安装器已结束；以下结论同时使用返回码和刷新后的本地清单验证。")
        self.details.setHtml(_report_html(report))
        self.primary_button.setText("关闭")
        self.primary_button.setEnabled(True)
        self.cancel_button.setText("关闭")
        self.residual_button.setVisible(
            report.verification.state
            in {
                MsiVerificationState.VERIFIED_REMOVED,
                MsiVerificationState.COMPLETED_UNVERIFIED,
                MsiVerificationState.REMOVED_WITH_UNEXPECTED_INSTALLER_RESULT,
            }
        )

    @Slot()
    def _open_residual_analysis(self) -> None:
        """Open a fresh R0 plan; the uninstall confirmation is never reused."""
        plan = self._plan
        if plan is None:
            return
        dialog = ResidualAnalysisDialog(
            self._runtime,
            plan.transaction_id,
            parent=self,
        )
        self._residual_dialog = dialog
        dialog.show()
        self.cancel_button.setEnabled(True)

    def _show_preview(self, preview: MsiUninstallPreview, *, immediate: bool) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.candidates.setVisible(False)
        self.details.setHtml(_preview_html(preview, immediate=immediate))
        self.risk_label.setText(
            "即时确认：对象、ProductCode、风险和 Preview 均已绑定；默认按钮仍是取消。"
            if immediate
            else "计划确认：不会授权旧 Preview 重放，执行前还会完整重新验证一次。"
        )

    @Slot()
    def _cancel_clicked(self) -> None:
        worker = self._cancellable_worker
        if worker is not None and hasattr(worker, "cancel"):
            worker.cancel()
            self._close_when_safe = True
            self.cancel_button.setEnabled(False)
            self.risk_label.setText(
                "已记录取消请求。若 Windows Installer 已启动，本应用不会强制终止它。"
            )
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
        self.risk_label.setText("流程已停止。没有自动重试、提权、关进程、停服务或删除残留。")
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
        """Request cancellation without terminating an already dispatched installer."""
        worker = self._cancellable_worker
        if worker is not None and hasattr(worker, "cancel"):
            worker.cancel()
        self._close_when_safe = True

    def closeEvent(self, event: QCloseEvent) -> None:
        """Reject pending approvals; keep launched MSI monitoring owned by its worker."""
        if self._worker is not None:
            self.shutdown()
            event.ignore()
            return
        if self._stage == "PLAN_CONFIRMATION":
            self._reject_plan_and_close()
        elif self._stage == "RUNTIME_CONFIRMATION":
            self._reject_runtime_and_close()
        event.accept()


def _preview_html(preview: MsiUninstallPreview, *, immediate: bool) -> str:
    target = preview.target
    preflight = preview.preflight
    blockers = "".join(f"<li>{escape(value)}</li>" for value in preflight.blockers)
    warnings = "".join(f"<li>{escape(value)}</li>" for value in preflight.warnings)
    phase = "执行前即时确认" if immediate else "计划确认"
    return (
        f"<h2>MSI 软件卸载 — {phase}</h2>"
        f"<p><b>软件：</b>{escape(target.display_name)} "
        f"{escape(target.display_version or '版本不可用')}</p>"
        f"<p><b>Publisher：</b>{escape(target.publisher or '不可用')}；"
        f"<b>范围：</b>{escape(target.scope.value)}；"
        f"<b>架构：</b>{escape(target.architecture.value)}</p>"
        f"<p><b>机制：</b>Windows Installer / MSI；"
        f"<b>风险：</b>{escape(preview.execution_assessment.risk_level.value)}；"
        f"<b>分类：</b>{escape(preview.execution_assessment.safety_class.value)}</p>"
        f"<p><b>ProductCode：</b><code>{escape(preview.validated_product.product_code)}</code></p>"
        f"<p><b>相关进程：</b>{len(preflight.related_processes)}；"
        f"<b>相关服务：</b>{len(preflight.related_services)}；"
        f"<b>Preflight：</b>{escape(preflight.state.value)}</p>"
        + (f"<h3>阻止原因</h3><ul>{blockers}</ul>" if blockers else "")
        + (f"<h3>限制</h3><ul>{warnings}</ul>" if warnings else "")
        + "<p><b>不会自动：</b>结束进程、停止服务、提权、重启、删除 AppData、"
        "删除文档、删除 ProgramData 或清理任何残留。</p>"
        "<p><b>Rollback：NONE。</b>恢复通常需要重新安装；重新安装不是 Undo，"
        "也不保证恢复设置或用户数据。</p>"
        f"<p><b>确认到期依据：</b>{escape(preview.expires_at.isoformat())}</p>"
    )


def _report_html(report: MsiUninstallExecutionReport) -> str:
    warnings = "".join(f"<li>{escape(value)}</li>" for value in report.verification.warnings)
    residual = report.residual
    return (
        "<h2>MSI 卸载结果</h2>"
        f"<p><b>目标：</b>{escape(report.target_summary)}</p>"
        f"<p><b>安装器结果：</b>{escape(report.installer.category.value)}；"
        f"<b>Exit Code：</b>{report.installer.exit_code}</p>"
        f"<p><b>最终验证：</b>{escape(report.verification.state.value)}；"
        f"<b>原身份仍存在：</b>{report.verification.original_identity_present}；"
        f"<b>ProductCode 仍存在：</b>{report.verification.original_product_code_present}</p>"
        f"<p><b>残留检查已执行：</b>{residual.checked_location}；"
        f"<b>已知安装目录仍存在：</b>{residual.install_location_present}；"
        f"<b>链接/重解析点：</b>{residual.reparse_or_symlink}</p>"
        + (f"<h3>验证警告</h3><ul>{warnings}</ul>" if warnings else "")
        + f"<p><b>恢复：</b>{escape(report.recovery_guidance)}</p>"
        "<p>残留仅报告，未删除。即使 Exit Code 为成功，也以此处的刷新验证为准。</p>"
    )
