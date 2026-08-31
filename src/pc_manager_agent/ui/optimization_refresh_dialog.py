"""Explicitly confirmed minimal R0 readback, separate from an action's success evidence."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.optimization_actions import OptimizationTargetDomain
from pc_manager_agent.domain.system_diagnostics import DiagnosticReport, SystemSnapshot
from pc_manager_agent.orchestration.optimization_refresh import (
    compare_optimization_observations,
    optimization_refresh_goal,
)
from pc_manager_agent.ui.system_diagnostics_tab import SystemDiagnosticsTab


class OptimizationRefreshDialog(QDialog):
    """Prepare a new domain-specific R0 plan; do not auto-approve, rescan files or execute."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        domain: OptimizationTargetDomain,
        baseline: SystemSnapshot,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._domain = domain
        self._baseline = baseline
        self.setWindowTitle("只读刷新：短期观察不代表性能提升")
        self.resize(1100, 740)
        layout = QVBoxLayout(self)
        self.observation = QLabel("需要新的 R0 计划确认；不会重跑全套扫描，不会修改系统。")
        self.observation.setWordWrap(True)
        layout.addWidget(self.observation)
        self.diagnostic = SystemDiagnosticsTab(runtime)
        self.diagnostic.start_planning(optimization_refresh_goal(domain))
        self.diagnostic.goal_input.setReadOnly(True)
        for button in (
            self.diagnostic.process_action_button,
            self.diagnostic.software_action_button,
            self.diagnostic.software_uninstall_button,
            self.diagnostic.vendor_uninstall_button,
        ):
            button.hide()
        self.diagnostic.report_ready.connect(self._report_ready)
        layout.addWidget(self.diagnostic)

    @Slot(object)
    def _report_ready(self, value: object) -> None:
        if not isinstance(value, DiagnosticReport):
            return
        try:
            observations = compare_optimization_observations(
                self._domain, self._baseline, value.snapshot
            )
        except Exception:
            self.observation.setText("无法可靠比较本次观察；未测得性能收益。")
            return
        text = "；".join(
            f"{item.metric.value}：{item.before} → {item.after}"
            if item.measured
            else f"{item.metric.value}：证据不完整，未测量"
            for item in observations
        )
        self.observation.setText(
            text + "。仅为短期观察，不能归因于此次操作；未测量启动耗时或速度提升。"
            "回收站移动不等于释放空间。"
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        """Cooperatively cancel pending reads on close."""
        self.diagnostic.shutdown()
        super().closeEvent(event)
