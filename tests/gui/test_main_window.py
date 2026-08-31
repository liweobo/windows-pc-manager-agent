from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.ui.main_window import MainWindow


@pytest.mark.gui
def test_main_window_has_safe_default_tabs_and_local_chat(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
) -> None:
    window = MainWindow(runtime)
    qtbot.addWidget(window)
    assert window._tabs.count() == 12
    assert window._tabs.tabText(11) == "办公文档"
    assert window._tabs.tabText(5) == "系统优化分析"
    assert window._tabs.tabText(6) == "启动项管理"
    assert window._tabs.tabText(7) == "服务管理"
    assert window._tabs.tabText(3) == "Windows 回收站"
    assert window._tabs.tabText(1) == "文件分析"
    assert window._tabs.tabText(2) == "安全文件操作"
    assert window._service_management_tab._worker is None
    assert not window._scan_button.isEnabled()
    window._chat_input.setText("delete everything")
    qtbot.keyClick(window._chat_input, Qt.Key.Key_Return)
    assert window._tabs.currentWidget() is window._trash_tab
    assert "回收站页面" in window._conversation.toPlainText()

    window._chat_input.setText("永久删除这个文件")
    qtbot.keyClick(window._chat_input, Qt.Key.Key_Return)
    assert "已拒绝永久删除" in window._conversation.toPlainText()
    assert runtime.audit.list_recent(1)[0].event_type == "trash.request_refused"


@pytest.mark.gui
def test_gui_plan_confirmation_and_background_scan(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "gui-files"
    root.mkdir()
    (root / "one.txt").write_text("one", encoding="utf-8")
    window = MainWindow(runtime)
    qtbot.addWidget(window)
    window._root_input.setText(str(root))
    window._prepare_plan()
    assert window._confirm_button.isEnabled()
    assert "R0" in window._risk_label.text()
    window._approve_plan()
    assert window._scan_button.isEnabled()
    window._start_scan()
    qtbot.waitUntil(lambda: window._worker is None, timeout=10_000)
    assert window._results.rowCount() == 1
    assert "完成" in window.statusBar().currentMessage()
