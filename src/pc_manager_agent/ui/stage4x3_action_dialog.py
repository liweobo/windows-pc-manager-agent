"""Object-specific two-confirmation and UAC UI for Stage 4X3 actions."""

from __future__ import annotations

from contextlib import suppress
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

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.orchestration.elevated_service_actions import ElevatedDispatchStatus
from pc_manager_agent.orchestration.privileged_actions import PrivilegedActionPreparationError
from pc_manager_agent.ui.stage4x3_workers import (
    PreparedStage4X3Ui,
    RuntimeStage4X3Ui,
    Stage4X3DispatchWorker,
    Stage4X3PrepareWorker,
    Stage4X3RuntimeWorker,
    Stage4X3UiRequest,
    require_prepared_stage4x3,
    require_runtime_stage4x3,
    require_stage4x3_outcome,
)


class Stage4X3ActionDialog(QDialog):
    """Keep exact target, R3 risk, two approvals, UAC, and verification visible."""

    completed = Signal()

    def __init__(
        self,
        runtime: ApplicationRuntime,
        request: Stage4X3UiRequest,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._runtime = runtime
        self._request = request
        self._prepared: PreparedStage4X3Ui | None = None
        self._runtime_value: RuntimeStage4X3Ui | None = None
        self._worker: object | None = None
        self._stage = "PREPARING"
        self.setWindowTitle("需要管理员权限 — Stage 4X3 安全操作")
        self.resize(780, 620)
        self.setModal(False)
        self._build_ui()
        self._prepare()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._risk = QLabel("正在只读重验身份、安全策略和备份；尚未显示 UAC 或修改系统。")
        self._risk.setWordWrap(True)
        self._risk.setStyleSheet("font-weight: 700; color: #9c3d10;")
        self._details = QTextBrowser()
        self._details.setOpenExternalLinks(False)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        buttons = QHBoxLayout()
        self._primary = QPushButton("等待安全 Preview")
        self._primary.setEnabled(False)
        self._primary.setAutoDefault(False)
        self._cancel = QPushButton("取消（默认）")
        self._cancel.setDefault(True)
        self._primary.clicked.connect(self._advance)
        self._cancel.clicked.connect(self._cancel_clicked)
        buttons.addStretch(1)
        buttons.addWidget(self._primary)
        buttons.addWidget(self._cancel)
        layout.addWidget(self._risk)
        layout.addWidget(self._details, 1)
        layout.addWidget(self._progress)
        layout.addLayout(buttons)

    def _prepare(self) -> None:
        worker = Stage4X3PrepareWorker(self._runtime, self._request)
        worker.signals.completed.connect(self._prepared_ready)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _prepared_ready(self, value: object) -> None:
        self._worker = None
        try:
            prepared = require_prepared_stage4x3(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._prepared = prepared
        self._stage = "PLAN_CONFIRMATION"
        self._progress.setRange(0, 1)
        self._risk.setText(
            "计划确认：R3 单对象管理员操作。此确认不会弹出 UAC，也不会授权不同对象。"
        )
        self._details.setHtml(_preview_html(prepared, runtime=False))
        self._primary.setText("确认管理员计划并重新验证")
        self._primary.setEnabled(True)

    @Slot()
    def _advance(self) -> None:
        if self._stage == "PLAN_CONFIRMATION":
            self._approve_plan()
        elif self._stage == "RUNTIME_CONFIRMATION":
            self._approve_runtime()
        elif self._stage in {"COMPLETED", "FAILED"}:
            self.accept()

    def _approve_plan(self) -> None:
        prepared = self._prepared
        if prepared is None:
            self._failed("计划状态不完整；未请求 UAC。")
            return
        try:
            prepared.service.approve_plan(prepared.prepared, True)
        except Exception as exc:
            self._failed(f"{type(exc).__name__}: {exc}")
            return
        worker = Stage4X3RuntimeWorker(prepared)
        worker.signals.completed.connect(self._runtime_ready)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        self._stage = "REVALIDATING"
        self._primary.setEnabled(False)
        self._progress.setRange(0, 0)
        self._risk.setText("计划已确认；正在再次读取目标和安全证据，尚未显示 UAC。")
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _runtime_ready(self, value: object) -> None:
        self._worker = None
        try:
            runtime = require_runtime_stage4x3(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        self._runtime_value = runtime
        self._stage = "RUNTIME_CONFIRMATION"
        self._progress.setRange(0, 1)
        self._risk.setText("即时确认：下一步将只显示一次 Windows UAC。取消 UAC 后不会自动重试。")
        self._details.setHtml(_preview_html(runtime.prepared_ui, runtime=True))
        self._primary.setText("即时确认并请求管理员权限")
        self._primary.setEnabled(True)

    def _approve_runtime(self) -> None:
        runtime = self._runtime_value
        if runtime is None:
            self._failed("即时确认状态不完整；未请求 UAC。")
            return
        worker = Stage4X3DispatchWorker(runtime)
        worker.signals.completed.connect(self._executed)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        self._stage = "UAC_AND_BROKER"
        self._primary.setEnabled(False)
        self._cancel.setEnabled(False)
        self._progress.setRange(0, 0)
        self._risk.setText("Windows UAC 可能正在显示。可在 UAC 中取消；Agent 不会自动重试。")
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _executed(self, value: object) -> None:
        self._worker = None
        try:
            outcome = require_stage4x3_outcome(value)
        except TypeError as exc:
            self._failed(str(exc))
            return
        verified = outcome.status is ElevatedDispatchStatus.VERIFIED
        self._stage = "COMPLETED" if verified else "FAILED"
        self._progress.setRange(0, 1)
        self._progress.setValue(1)
        self._risk.setText(
            "Broker 验证和普通进程独立回读均通过。"
            if verified
            else "无法确认操作完整成功；不会自动重试。请刷新只读清单核对。"
        )
        failure = outcome.failure_code.value if outcome.failure_code else "无"
        self._details.setHtml(
            f"<h3>Stage 4X3 结果：{escape(outcome.status.value)}</h3>"
            f"<p>{escape(outcome.message)}</p><p>失败代码：{escape(failure)}</p>"
            "<p>恢复或重装必须作为新的独立任务，再次确认并重新请求 UAC。</p>"
        )
        self._primary.setText("关闭")
        self._primary.setEnabled(True)
        self._cancel.setEnabled(True)
        self._cancel.setText("关闭")
        if verified:
            self.completed.emit()

    @Slot(str)
    def _failed(self, message: str) -> None:
        self._worker = None
        self._stage = "FAILED"
        self._progress.setRange(0, 1)
        self._risk.setText("管理员操作未完成；没有自动重试或降级到其他执行方式。")
        self._details.setPlainText(message)
        self._primary.setText("关闭")
        self._primary.setEnabled(True)
        self._cancel.setEnabled(True)
        self._cancel.setText("关闭")

    @Slot()
    def _cancel_clicked(self) -> None:
        if self._stage == "PLAN_CONFIRMATION" and self._prepared is not None:
            self._prepared.service.approve_plan(self._prepared.prepared, False)
        elif self._stage == "RUNTIME_CONFIRMATION" and self._runtime_value is not None:
            value = self._runtime_value
            with suppress(PrivilegedActionPreparationError):
                value.prepared_ui.service.approve_runtime_and_build(
                    value.prepared_ui.prepared,
                    value.runtime,
                    False,
                )
        if self._stage != "UAC_AND_BROKER":
            self.reject()

    def shutdown(self) -> None:
        """Reject any undispatched confirmation; an active Broker is never terminated."""
        if self._stage != "UAC_AND_BROKER":
            self._cancel_clicked()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Keep UAC/Broker lifecycle visible and reject pending approvals on close."""
        if self._stage == "UAC_AND_BROKER":
            event.ignore()
            return
        self._cancel_clicked()
        event.accept()


def _preview_html(value: PreparedStage4X3Ui, *, runtime: bool) -> str:
    plan = value.prepared.privileged.plan
    preview = value.prepared.privileged.preview
    rollback = {
        "SERVICE_STARTUP_TYPE_CHANGE": RollbackLevel.FULL.value,
        "SERVICE_STARTUP_TYPE_RESTORE": RollbackLevel.FULL.value,
        "STARTUP_MACHINE_DISABLE": RollbackLevel.FULL.value,
        "STARTUP_MACHINE_RESTORE": RollbackLevel.FULL.value,
        "MSI_UNINSTALL_MACHINE": RollbackLevel.NONE.value,
    }.get(plan.action_type.value, RollbackLevel.MANUAL.value)
    phase = "即时重验 Preview" if runtime else "管理员计划 Preview"
    recovery = (
        "可条件式完整恢复；目标被外部修改时必须停止。"
        if rollback == RollbackLevel.FULL.value
        else "无法自动回滚；需要用户手动重新安装。"
    )
    return (
        f"<h3>{phase}</h3>"
        "<table border='1' cellspacing='0' cellpadding='4'>"
        f"<tr><th>对象</th><td>{escape(value.request.display_name)}</td></tr>"
        f"<tr><th>动作</th><td>{escape(plan.action_type.value)}</td></tr>"
        "<tr><th>数量</th><td>1</td></tr>"
        "<tr><th>风险</th><td>R3</td></tr>"
        "<tr><th>所需权限</th><td>Windows Administrator（一次性 UAC）</td></tr>"
        f"<tr><th>回滚</th><td>{rollback}</td></tr>"
        f"<tr><th>计划 ID</th><td>{plan.plan_id}</td></tr>"
        f"<tr><th>到期时间</th><td>{preview.generated_at.astimezone().isoformat()}</td></tr>"
        "</table>"
        f"<p>{recovery}</p>"
        "<p>Broker 只接收结构化身份与摘要，不接收命令、脚本或任意参数。</p>"
    )
