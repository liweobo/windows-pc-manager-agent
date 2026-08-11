from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.ui.operation_tab import FileOperationTab


@pytest.mark.gui
@pytest.mark.windows
def test_operation_tab_previews_confirms_executes_and_rolls_back(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "authorized"
    source_directory = root / "source"
    destination_directory = root / "destination"
    source_directory.mkdir(parents=True)
    destination_directory.mkdir()
    source = source_directory / "report.txt"
    source.write_text("report", encoding="utf-8")
    destination = destination_directory / source.name
    runtime.authorized_paths.add_authorized(root)
    tab = FileOperationTab(runtime)
    qtbot.addWidget(tab)
    tab.set_sources((source,))
    tab.destination_input.setText(str(destination_directory))

    tab._prepare_move()
    qtbot.waitUntil(lambda: tab._preview_worker is None, timeout=10_000)
    assert tab._prepared is not None
    assert tab.confirm_button.isEnabled()
    assert "不会覆盖" in tab.preview_summary.toPlainText()

    monkeypatch.setattr(
        "pc_manager_agent.ui.operation_tab.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    tab._confirm_forward()
    assert tab.execute_button.isEnabled()
    tab._execute_forward()
    qtbot.waitUntil(lambda: tab._execution_worker is None, timeout=10_000)
    assert destination.exists() and not source.exists()
    assert "COMPLETED" in tab.progress_label.text()

    tab.history_table.selectRow(0)
    tab._prepare_rollback()
    assert tab._prepared_rollback is not None
    assert tab.rollback_confirm_button.isEnabled()
    tab._confirm_and_execute_rollback()
    qtbot.waitUntil(lambda: tab._rollback_worker is None, timeout=10_000)
    assert source.exists() and not destination.exists()
    assert "ROLLED_BACK" in tab.progress_label.text()


@pytest.mark.gui
def test_operation_tab_requires_selection_and_never_executes_on_preview_error(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(
        "pc_manager_agent.ui.operation_tab.QMessageBox.warning",
        lambda _parent, _title, message: warnings.append(message),
    )
    tab = FileOperationTab(runtime)
    qtbot.addWidget(tab)
    tab._prepare_move()
    assert warnings == ["请先选择对象并填写已授权的目标目录。"]
    assert tab._execution_worker is None
