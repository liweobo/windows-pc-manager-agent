from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.memory import MemoryKey
from pc_manager_agent.ui.memory_tab import MemoryTab


def test_memory_tab_saves_displays_and_deletes_explicit_preference(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tab = MemoryTab(runtime.agents.runtime.memory)
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    tab.key.setCurrentIndex(tab.key.findData(MemoryKey.RESPONSE_LANGUAGE.value))
    tab.value.setText("zh-CN")
    tab._save()
    assert tab.table.rowCount() == 1
    assert tab.table.item(0, 3).text() == "zh-CN"
    tab.table.selectRow(0)
    tab._delete()
    assert tab.table.rowCount() == 0
