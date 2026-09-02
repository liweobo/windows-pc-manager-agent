from __future__ import annotations

from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.task_graph import TaskDomain
from pc_manager_agent.ui.task_center_tab import TaskCenterTab


def test_task_center_shows_metadata_without_confirmation_button(
    qtbot: QtBot, runtime: ApplicationRuntime
) -> None:
    tab = TaskCenterTab(runtime.agents)
    qtbot.addWidget(tab)
    prepared = runtime.agents.runtime.prepare_domains("inspect system", (TaskDomain.SYSTEM,))
    tab.register_task(prepared)
    assert tab.table.rowCount() == 1
    assert tab.table.item(0, 2).text() == "inspect system"
    button_text = " ".join(button.text() for button in tab.findChildren(QPushButton))
    assert "批准全部" not in button_text
