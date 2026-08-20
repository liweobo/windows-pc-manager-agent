"""Stage 4D1 software identity, impact, and zero-execution Preview dialog."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, QThreadPool, Slot
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

from pc_manager_agent.app.runtime import ApplicationRuntime, SoftwareAnalysisServices
from pc_manager_agent.confirmation.models import ConfirmationRequest
from pc_manager_agent.domain.software_uninstall_analysis import (
    NormalizedInstalledSoftware,
    SoftwareTargetAcknowledgement,
    SoftwareTargetQuery,
    SoftwareUninstallAnalysisPlan,
    SoftwareUninstallPreview,
)
from pc_manager_agent.ui.software_workers import (
    PreparedSoftwareAnalysis,
    SoftwareAnalyzeWorker,
    SoftwarePrepareWorker,
    require_prepared_software_analysis,
    require_software_analysis_outcome,
)


class SoftwareAnalysisDialog(QDialog):
    """Show plan, candidates, Preview, understanding acknowledgement, and mandatory STOP."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        user_goal: str,
        *,
        query: SoftwareTargetQuery | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._user_goal = user_goal
        self._query = query
        self._services: SoftwareAnalysisServices | None = None
        self._plan: SoftwareUninstallAnalysisPlan | None = None
        self._plan_confirmation: ConfirmationRequest | None = None
        self._preview: SoftwareUninstallPreview | None = None
        self._acknowledgement: SoftwareTargetAcknowledgement | None = None
        self._worker: object | None = None
        self._analysis_worker: SoftwareAnalyzeWorker | None = None
        self._stage = "PREPARING"
        self._discard_after_worker = False
        self.setWindowTitle("软件卸载分析 — Stage 4D1 永不执行")
        self.resize(940, 720)
        self.setModal(False)
        self._build_ui()
        self._start_prepare(query)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.risk_label = QLabel(
            "Stage 4D1 只读取身份和影响信息；不会运行 MSI、卸载器、winget 或删除 API。"
        )
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
        self.primary_button = QPushButton("等待只读计划")
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
        layout.addWidget(self.details, 2)
        layout.addWidget(self.candidates, 1)
        layout.addWidget(self.progress)
        layout.addLayout(buttons)

    def _start_prepare(self, query: SoftwareTargetQuery | None) -> None:
        self._set_busy("正在生成只读软件分析计划；尚未读取卸载命令。")
        worker = SoftwarePrepareWorker(self._runtime, self._user_goal, query)
        worker.signals.completed.connect(self._prepared)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _prepared(self, value: object) -> None:
        self._worker = None
        try:
            prepared = require_prepared_software_analysis(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._services = prepared.services
        self._plan = prepared.plan
        self._show_plan(prepared)

    def _show_plan(self, prepared: PreparedSoftwareAnalysis) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.candidates.setVisible(False)
        issues = "<br>".join(escape(issue.message) for issue in prepared.review.issues)
        self.details.setHtml(
            "<h3>R0 只读分析计划</h3>"
            f"<p><b>用户目标：</b>{escape(prepared.plan.user_goal)}</p>"
            f"<p><b>目标查询：</b>{escape(prepared.plan.target_query.model_dump_json())}</p>"
            f"<p><b>工具：</b>{escape(', '.join(prepared.plan.tool_names))}</p>"
            "<p><b>预计系统修改：</b>0；<b>管理员权限：</b>不请求；"
            "<b>卸载执行：</b>不存在。</p>"
            + (f"<p><b>安全问题：</b>{issues}</p>" if issues else "")
        )
        if not prepared.review.approved:
            self._stage = "BLOCKED"
            self.risk_label.setText("计划被安全层阻止；未执行任何软件操作。")
            self.primary_button.setText("关闭")
            self.primary_button.setEnabled(True)
            return
        try:
            confirmation = prepared.services.service.request_plan_confirmation(prepared.plan)
        except Exception as exc:
            self._failed(f"{type(exc).__name__}: {exc}")
            return
        self._plan_confirmation = confirmation
        self._stage = "PLAN_CONFIRMATION"
        self.risk_label.setText("等待确认 R0 计划；确认后只会读取元数据并生成 Preview。")
        self.primary_button.setText("确认只读计划并生成 Preview")
        self.primary_button.setEnabled(True)
        if self._discard_after_worker:
            self._reject_plan_and_close()

    @Slot()
    def _primary_clicked(self) -> None:
        if self._stage == "PLAN_CONFIRMATION":
            self._approve_plan_and_analyze()
        elif self._stage == "CANDIDATE_SELECTION":
            self._select_candidate()
        elif self._stage == "TARGET_ACKNOWLEDGEMENT":
            self._acknowledge_and_stop()
        elif self._stage in {"STOPPED", "FAILED", "BLOCKED"}:
            self.accept()

    def _approve_plan_and_analyze(self) -> None:
        services = self._services
        plan = self._plan
        confirmation = self._plan_confirmation
        if services is None or plan is None or confirmation is None:
            return
        try:
            services.service.resolve_plan_confirmation(confirmation.confirmation_id, True, plan)
        except Exception as exc:
            self._failed(f"{type(exc).__name__}: {exc}")
            return
        worker = SoftwareAnalyzeWorker(services, plan)
        worker.signals.completed.connect(self._analyzed)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        self._analysis_worker = worker
        self._stage = "ANALYZING"
        self._set_busy("正在刷新身份、能力和影响信息；所有步骤均为 R0 只读。")
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _analyzed(self, value: object) -> None:
        self._worker = None
        self._analysis_worker = None
        try:
            outcome = require_software_analysis_outcome(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        if outcome.preview is None:
            self._show_candidates(outcome.resolution.candidates, outcome.resolution.reason)
            return
        self._preview = outcome.preview
        self._show_preview(outcome.preview)

    def _show_candidates(
        self,
        candidates: tuple[NormalizedInstalledSoftware, ...],
        reason: str,
    ) -> None:
        self._stage = "CANDIDATE_SELECTION"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.details.setHtml(
            "<h3>需要明确选择</h3>"
            f"<p>{escape(reason)}</p>"
            "<p>模型不会替你选择软件。请选择一行，系统将创建全新的计划，旧确认立即失效。</p>"
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
                    cell.setData(
                        Qt.ItemDataRole.UserRole,
                        item.identity.canonical_digest(),
                    )
                self.candidates.setItem(row, column, cell)
        self.candidates.setVisible(True)
        if candidates:
            self.candidates.selectRow(0)
            self.primary_button.setEnabled(True)
            self.primary_button.setText("选择此目标并重新生成计划")
        else:
            self.primary_button.setEnabled(False)
            self.risk_label.setText("没有匹配候选；未执行任何操作。")

    def _select_candidate(self) -> None:
        row = self.candidates.currentRow()
        item = self.candidates.item(row, 0) if row >= 0 else None
        identity_digest = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(identity_digest, str):
            self.risk_label.setText("请先选择一个具体软件候选。")
            return
        self._plan = None
        self._plan_confirmation = None
        self._preview = None
        self._acknowledgement = None
        self._start_prepare(SoftwareTargetQuery(identity_digest=identity_digest))

    def _show_preview(self, preview: SoftwareUninstallPreview) -> None:
        services = self._services
        plan = self._plan
        if services is None or plan is None:
            self._failed("Internal state error: software analysis services are unavailable")
            return
        try:
            acknowledgement = services.service.request_target_acknowledgement(plan, preview)
        except Exception as exc:
            self._failed(f"{type(exc).__name__}: {exc}")
            return
        self._acknowledgement = acknowledgement
        self._stage = "TARGET_ACKNOWLEDGEMENT"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.candidates.setVisible(False)
        self.details.setHtml(_preview_html(preview))
        self.risk_label.setText(
            "Preview 已生成：executable_in_current_stage=false。确认只表示理解目标。"
        )
        self.primary_button.setText("确认我理解该目标（不会卸载）")
        self.primary_button.setEnabled(True)
        self.cancel_button.setText("拒绝并关闭")
        if self._discard_after_worker:
            self._reject_acknowledgement_and_close()

    def _acknowledge_and_stop(self) -> None:
        services = self._services
        plan = self._plan
        preview = self._preview
        acknowledgement = self._acknowledgement
        if services is None or plan is None or preview is None or acknowledgement is None:
            return
        try:
            services.service.resolve_target_acknowledgement(
                acknowledgement.acknowledgement_id,
                True,
                plan,
                preview,
            )
        except Exception as exc:
            self._failed(f"{type(exc).__name__}: {exc}")
            return
        self._stage = "STOPPED"
        self.risk_label.setText("已记录你理解的目标。Stage 4D1 已停止，未卸载任何软件。")
        self.details.append("<hr><h3>STOP</h3><p>execution_performed=false；没有创建执行授权。</p>")
        self.primary_button.setText("关闭")
        self.cancel_button.setText("关闭")

    @Slot()
    def _cancel_clicked(self) -> None:
        if self._analysis_worker is not None:
            self._analysis_worker.cancel()
            self._discard_after_worker = True
            self.cancel_button.setEnabled(False)
            self.risk_label.setText("已请求取消剩余只读分析；不会开始任何卸载操作。")
            return
        if self._worker is not None:
            self._discard_after_worker = True
            self.cancel_button.setEnabled(False)
            return
        if self._stage == "PLAN_CONFIRMATION":
            self._reject_plan_and_close()
            return
        if self._stage == "TARGET_ACKNOWLEDGEMENT":
            self._reject_acknowledgement_and_close()
            return
        self.reject()

    def _reject_plan_and_close(self) -> None:
        if self._services and self._plan and self._plan_confirmation:
            try:
                self._services.service.resolve_plan_confirmation(
                    self._plan_confirmation.confirmation_id,
                    False,
                    self._plan,
                )
            finally:
                self.reject()
            return
        self.reject()

    def _reject_acknowledgement_and_close(self) -> None:
        if self._services and self._plan and self._preview and self._acknowledgement:
            try:
                self._services.service.resolve_target_acknowledgement(
                    self._acknowledgement.acknowledgement_id,
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
        self._analysis_worker = None
        self._stage = "FAILED"
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.risk_label.setText("分析失败并已停止；未卸载、修改或删除任何软件。")
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
        """Cancel remaining read-only work and reject pending understanding state."""
        if self._analysis_worker is not None:
            self._analysis_worker.cancel()
        self._discard_after_worker = True

    def closeEvent(self, event: QCloseEvent) -> None:
        """Treat window close as rejection/cancellation, never implicit approval."""
        self.shutdown()
        event.accept()


def _preview_html(preview: SoftwareUninstallPreview) -> str:
    target = preview.target
    capability = preview.capability
    impacts = "".join(
        "<li>"
        f"[{escape(item.kind.value)} / {escape(item.severity.value)}] "
        f"<b>{escape(item.title)}</b> — {escape(item.explanation)}"
        "</li>"
        for item in preview.impact.findings
    )
    warnings = "".join(
        f"<li>{escape(item)}</li>" for item in (*capability.warnings, *preview.impact.warnings)
    )
    return (
        "<h2>Stage 4D1 软件卸载分析 Preview</h2>"
        f"<p><b>目标：</b>{escape(target.display_name)} "
        f"{escape(target.display_version or '版本不可用')}</p>"
        f"<p><b>发布者：</b>{escape(target.publisher or '不可用')}；"
        f"<b>范围：</b>{escape(target.scope.value)}；"
        f"<b>架构：</b>{escape(target.architecture.value)}；"
        f"<b>来源：</b>{escape(target.source.value)}</p>"
        f"<p><b>身份摘要：</b><code>{escape(preview.identity_digest)}</code></p>"
        f"<p><b>安全分类：</b>{escape(preview.safety.safety_class.value)}；"
        f"<b>决策：</b>{escape(preview.safety.decision.value)}</p>"
        f"<p><b>能力：</b>{escape(capability.capability_type.value)} / "
        f"{escape(capability.support.value)} / confidence={escape(capability.confidence)}</p>"
        f"<p><b>阻止状态：</b>{preview.blocked}；{escape(preview.stop_reason)}</p>"
        "<p><b>本阶段可执行：</b>false；<b>执行发生：</b>false；"
        f"<b>未来恢复：</b>{escape(preview.future_recovery_level.value)}</p>"
        f"<p><b>Preview 到期：</b>{escape(preview.expires_at.isoformat())}</p>"
        f"<h3>影响证据与警告</h3><ul>{impacts}</ul>"
        + (f"<h3>其他限制</h3><ul>{warnings}</ul>" if warnings else "")
        + f"<p><i>{escape(preview.impact.disclaimer)}</i></p>"
        "<hr><p><b>没有卸载按钮。确认后流程立即 STOP。</b></p>"
    )
