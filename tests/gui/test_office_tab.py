from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from tests.integration.office.test_edit_flow import Harness
from tests.integration.office.test_edit_flow import harness as harness

from pc_manager_agent.app.office import OfficeServices
from pc_manager_agent.domain.office_transactions import OfficeTransactionState
from pc_manager_agent.ui.office_tab import OfficeTab


@pytest.fixture
def office_tab(qtbot, harness: Harness):
    tab = OfficeTab(OfficeServices(harness.reads, harness.edits, None, harness.repository))
    qtbot.addWidget(tab)
    yield tab
    assert tab.shutdown()


def idle(qtbot, tab):
    qtbot.waitUntil(lambda: tab._worker is None, timeout=10_000)


@pytest.mark.gui
def test_office_read_saveas_diff_and_history(qtbot, office_tab, tmp_path: Path, monkeypatch):
    source = tmp_path / "document.txt"
    output = tmp_path / "result.txt"
    source.write_text("source text", encoding="utf-8")
    monkeypatch.setattr(
        "pc_manager_agent.ui.office_tab.QFileDialog.getOpenFileNames",
        lambda *args, **kwargs: ([str(source)], ""),
    )
    monkeypatch.setattr(
        "pc_manager_agent.ui.office_tab.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: (str(output), ""),
    )
    monkeypatch.setattr(office_tab, "_ask", lambda *args: True)
    office_tab._choose_documents()
    idle(qtbot, office_tab)
    assert office_tab._documents.count() == 1
    item = office_tab._documents.item(0)
    assert item.checkState() is Qt.CheckState.Unchecked
    office_tab._documents.setCurrentRow(0)
    assert "source text" in office_tab._content.toPlainText()
    item.setCheckState(Qt.CheckState.Checked)
    office_tab._choose_output()
    office_tab._target.setText("body")
    office_tab._value.setPlainText("new text")
    office_tab._add_operation()
    office_tab._prepare()
    idle(qtbot, office_tab)
    assert office_tab._preview is not None
    assert "source text" in office_tab._diff.toPlainText()
    assert "new text" in office_tab._diff.toPlainText()
    office_tab._execute()
    idle(qtbot, office_tab)
    assert not output.exists()
    office_tab._confirm_plan()
    idle(qtbot, office_tab)
    office_tab._execute()
    idle(qtbot, office_tab)
    assert output.read_text(encoding="utf-8") == "new text"
    assert source.read_text(encoding="utf-8") == "source text"
    office_tab._refresh_history()
    idle(qtbot, office_tab)
    assert office_tab._history.count() == 1


@pytest.mark.gui
def test_office_creation_cancel_preview_and_model_disabled(
    qtbot, office_tab, tmp_path: Path, monkeypatch
):
    output = tmp_path / "new.txt"
    monkeypatch.setattr(
        "pc_manager_agent.ui.office_tab.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: (str(output), ""),
    )
    office_tab._choose_output()
    office_tab._mode.setCurrentIndex(1)
    office_tab._value.setPlainText("created synthetic text")
    office_tab._prepare()
    idle(qtbot, office_tab)
    assert office_tab._preview
    office_tab._value.setPlainText("changed after preview")
    assert office_tab._preview is None
    office_tab._prepare_model()
    assert "MODEL_DISABLED" in office_tab._status.text()
    assert not output.exists()


@pytest.mark.gui
def test_office_inplace_backup_and_immediate_confirmation(
    qtbot, office_tab, tmp_path: Path, monkeypatch
):
    source = tmp_path / "inplace.txt"
    source.write_text("original", encoding="utf-8")
    monkeypatch.setattr(
        "pc_manager_agent.ui.office_tab.QFileDialog.getOpenFileNames",
        lambda *args, **kwargs: ([str(source)], ""),
    )
    monkeypatch.setattr(
        "pc_manager_agent.ui.office_tab.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: (str(source), ""),
    )
    monkeypatch.setattr(office_tab, "_ask", lambda *args: True)
    office_tab._choose_documents()
    idle(qtbot, office_tab)
    office_tab._documents.item(0).setCheckState(Qt.CheckState.Checked)
    office_tab._choose_output()
    office_tab._mode.setCurrentIndex(2)
    office_tab._target.setText("body")
    office_tab._value.setPlainText("edited")
    office_tab._add_operation()
    office_tab._prepare()
    idle(qtbot, office_tab)
    office_tab._confirm_plan()
    idle(qtbot, office_tab)
    assert "BACKUP_REQUIRED" in office_tab._status.text()
    office_tab._backup()
    idle(qtbot, office_tab)
    assert office_tab._preview.backup_id
    office_tab._confirm_plan()
    idle(qtbot, office_tab)
    office_tab._execute()
    idle(qtbot, office_tab)
    assert source.read_text(encoding="utf-8") == "edited"


@pytest.mark.gui
@pytest.mark.parametrize("state", [OfficeTransactionState.FAILED, OfficeTransactionState.CANCELLED])
def test_terminal_preview_can_be_discarded_without_reusing_authority(
    qtbot, office_tab, harness: Harness, tmp_path: Path, monkeypatch, state
):
    output = tmp_path / "new.txt"
    monkeypatch.setattr(
        "pc_manager_agent.ui.office_tab.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: (str(output), ""),
    )
    office_tab._choose_output()
    office_tab._mode.setCurrentIndex(1)
    office_tab._value.setPlainText("synthetic")
    office_tab._prepare()
    idle(qtbot, office_tab)
    old = office_tab._preview.transaction_id
    transaction = harness.repository.transaction(old)
    harness.repository.change(transaction, transaction.model_copy(update={"state": state}))
    office_tab._discard()
    assert office_tab._preview is None
    office_tab._prepare()
    idle(qtbot, office_tab)
    assert office_tab._preview.transaction_id != old
    office_tab._execute()
    idle(qtbot, office_tab)
    assert not output.exists()
