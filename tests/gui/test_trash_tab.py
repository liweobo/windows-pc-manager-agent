from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.ui.trash_tab import TrashTab


@pytest.mark.gui
def test_trash_tab_requires_explicit_selection_and_starts_with_no_confirmation(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(
        "pc_manager_agent.ui.trash_tab.QMessageBox.warning",
        lambda _parent, _title, message: warnings.append(message),
    )
    tab = TrashTab(runtime)
    qtbot.addWidget(tab)
    assert not tab.plan_confirm_button.isEnabled()
    assert not tab.runtime_confirm_button.isEnabled()
    tab._prepare()
    assert warnings == ["请先明确选择文件或文件夹。"]
    assert tab._execution_worker is None


@pytest.mark.gui
def test_trash_selection_change_invalidates_both_confirmations(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    tab = TrashTab(runtime)
    qtbot.addWidget(tab)
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    tab.set_sources((first,))
    assert tab._source_paths() == (first,)
    tab.plan_confirm_button.setEnabled(True)
    tab.runtime_confirm_button.setEnabled(True)
    tab.set_sources((second,))
    assert tab._source_paths() == (second,)
    assert tab._prepared is None
    assert tab._runtime_prepared is None
    assert not tab.plan_confirm_button.isEnabled()
    assert not tab.runtime_confirm_button.isEnabled()
