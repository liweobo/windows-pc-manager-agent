from __future__ import annotations

from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.system_optimization import OptimizationToolName
from pc_manager_agent.ui.system_optimization_tab import SystemOptimizationTab


def test_dashboard_exposes_analysis_controls_but_no_cleanup_action(
    qtbot: QtBot, runtime: ApplicationRuntime
) -> None:
    tab = SystemOptimizationTab(runtime)
    qtbot.addWidget(tab)
    labels = {button.text().casefold() for button in tab.findChildren(QPushButton)}
    assert "生成只读计划" in labels
    assert "开始只读分析" in labels
    assert all(
        token not in label for label in labels for token in ("一键清理", "boost", "fix", "apply")
    )
    assert tab._services.registry.names == tuple(
        sorted(item.value for item in OptimizationToolName)
    )
    tab.prepare()
    assert tab.confirm_button.isEnabled()
    assert not tab.run_button.isEnabled()
    assert "系统采集器" in tab.plan_view.toPlainText()
