from __future__ import annotations

from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.computer_tasks import ComputerTaskState
from pc_manager_agent.ui.home_tab import HomeTaskTab
from pc_manager_agent.ui.task_center_tab import TaskCenterTab


def test_home_creates_unconfirmed_task_and_task_center_has_no_global_approval(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
) -> None:
    home = HomeTaskTab(runtime.tasks)
    center = TaskCenterTab(runtime.tasks)
    qtbot.addWidget(home)
    qtbot.addWidget(center)
    created = []
    home.task_created.connect(created.append)
    home.table.selectRow(0)
    home.create_selected()
    assert len(created) == 1
    task = created[0]
    assert task.state is ComputerTaskState.AWAITING_PLAN_CONFIRMATION
    center.refresh()
    assert center.table.rowCount() == 1
    button_text = " ".join(button.text() for button in center.findChildren(QPushButton))
    assert "批准全部" not in button_text
    assert "全部确认" not in button_text
    assert "全局 Undo" not in button_text
