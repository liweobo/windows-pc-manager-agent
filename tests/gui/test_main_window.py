from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.config.production import FeatureFlags
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.ui.main_window import MainWindow
from pc_manager_agent.ui.safe_mode_window import SafeModeWindow


@pytest.mark.gui
def test_main_window_has_safe_default_tabs_and_local_chat(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
) -> None:
    window = MainWindow(runtime)
    qtbot.addWidget(window)
    assert window._tabs.count() == 16
    assert window._tabs.tabText(11) == "任务中心"
    assert window._tabs.tabText(12) == "记忆与偏好"
    assert window._tabs.tabText(13) == "办公文档"
    assert window._tabs.tabText(14) == "受控浏览器"
    assert window._tabs.tabText(15) == "主页与长任务"
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


@pytest.mark.gui
def test_private_rc_hides_and_blocks_non_release_domains(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    runtime = ApplicationRuntime(
        AppSettings(
            data_directory=tmp_path / "private-rc",
            feature_flags=FeatureFlags.private_rc_defaults(),
        )
    )
    window = MainWindow(runtime)
    qtbot.addWidget(window)
    try:
        assert window._tabs.isTabVisible(window._tabs.indexOf(window._analysis_tab))
        assert window._tabs.isTabVisible(window._tabs.indexOf(window._system_diagnostics_tab))
        assert window._tabs.isTabVisible(window._tabs.indexOf(window._system_optimization_tab))
        assert not window._tabs.isTabVisible(window._tabs.indexOf(window._operation_tab))
        assert not window._tabs.isTabVisible(window._tabs.indexOf(window._trash_tab))
        assert not window._tabs.isTabVisible(window._tabs.indexOf(window._browser_tab))
        assert window._voice is None
        assert window.accessibleName() == "Windows PC Manager Agent 主窗口"
        assert window._tabs.accessibleName() == "主要功能页面"
        assert window._chat_input.accessibleName() == "聊天输入"
        assert window._root_input.accessibleName() == "授权扫描目录"
        assert window._plan_view.accessibleName() == "结构化扫描计划"
        assert window._results.accessibleName() == "只读扫描结果"

        before_events = runtime.audit.list_recent(100)
        window._chat_input.setText("把文件移动到另一个目录")
        qtbot.keyClick(window._chat_input, Qt.Key.Key_Return)

        assert "当前发布版本未启用" in window._conversation.toPlainText()
        assert runtime.audit.list_recent(100) == before_events
    finally:
        runtime.close()


@pytest.mark.gui
def test_safe_mode_keeps_only_minimal_surfaces(qtbot: QtBot, tmp_path: Path) -> None:
    runtime = ApplicationRuntime(
        AppSettings(
            data_directory=tmp_path / "safe-mode",
            safe_mode=True,
            feature_flags=FeatureFlags(),
        )
    )
    window = SafeModeWindow(runtime)
    qtbot.addWidget(window)
    try:
        visible = {window._tabs.tabText(index) for index in range(window._tabs.count())}
        assert visible == {"安全模式", "审计", "设置"}
        assert "安全模式" in window.statusBar().currentMessage()
        assert window.accessibleName() == "Windows PC Manager Agent 安全模式窗口"
        assert window._tabs.accessibleName() == "安全模式页面"
        assert window._audit.accessibleName() == "安全模式审计记录"
    finally:
        runtime.close()
