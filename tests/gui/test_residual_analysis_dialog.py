"""GUI safety and usability coverage for the Stage 4D3 report-only workflow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox, QPushButton
from pytestqt.qtbot import QtBot
from tests.fixtures.software_residuals import (
    build_residual_environment,
    residual_context,
)

from pc_manager_agent.app.runtime import ResidualAnalysisServices
from pc_manager_agent.domain.software_residuals import ResidualClassification
from pc_manager_agent.reporting.residual_exporter import (
    ResidualReportExporter,
    ResidualReportFormat,
)
from pc_manager_agent.ui.residual_analysis_dialog import ResidualAnalysisDialog


@pytest.mark.gui
def test_residual_dialog_reports_filters_exports_and_has_no_cleanup_control(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "Example"
    cache = root / "cache"
    cache.mkdir(parents=True)
    (cache / "item.bin").write_bytes(b"cache")
    (root / "user.db").write_bytes(b"protected database")
    environment = build_residual_environment(tmp_path / "state.db")
    context = residual_context(root)
    environment.repository.upsert_context(context)
    explorer = MagicMock()
    services = ResidualAnalysisServices(
        registry=environment.registry,
        repository=environment.repository,
        service=environment.service,
        exporter=ResidualReportExporter(),
        explorer=explorer,
    )
    runtime = MagicMock()
    runtime.create_residual_analysis_services.return_value = services
    dialog = ResidualAnalysisDialog(runtime, context.transaction_id)
    qtbot.addWidget(dialog)
    try:
        qtbot.waitUntil(lambda: dialog._stage == "PLAN", timeout=10_000)
        assert dialog.cancel.isDefault()
        button_texts = {button.text() for button in dialog.findChildren(QPushButton)}
        assert not button_texts.intersection({"删除", "清理", "全部清理", "移入回收站"})
        dialog.primary.click()
        qtbot.waitUntil(lambda: dialog._stage == "DONE", timeout=10_000)
        assert dialog._report is not None
        assert dialog._report.deletion_performed is False
        assert dialog.table.rowCount() >= 4
        assert any(
            candidate.classification is ResidualClassification.DATABASE
            for candidate in dialog._report.candidates
        )

        dialog.search.setText("user.db")
        visible_rows = sum(
            not dialog.table.isRowHidden(row) for row in range(dialog.table.rowCount())
        )
        assert visible_rows == 1
        dialog.search.clear()
        database_index = dialog.classification_filter.findData("database")
        dialog.classification_filter.setCurrentIndex(database_index)
        visible_rows = sum(
            not dialog.table.isRowHidden(row) for row in range(dialog.table.rowCount())
        )
        assert visible_rows == 1

        for row in range(dialog.table.rowCount()):
            if not dialog.table.isRowHidden(row):
                dialog.table.setCurrentCell(row, 0)
                break
        dialog._open_selected_location()
        explorer.select_candidate.assert_called_once()

        target = tmp_path / "residual-report.json"
        monkeypatch.setattr(
            QFileDialog,
            "getSaveFileName",
            lambda *_args, **_kwargs: (str(target), "JSON"),
        )
        monkeypatch.setattr(QMessageBox, "information", lambda *_args, **_kwargs: None)
        dialog._export(ResidualReportFormat.JSON)
        qtbot.waitUntil(target.exists, timeout=10_000)
        assert '"deletion_performed": false' in target.read_text(encoding="utf-8")
    finally:
        dialog.close()
        environment.close()


def test_residual_dialog_source_does_not_expose_stage4d4_authority() -> None:
    """The Stage 4D3 UI can explain a future flow but has no cleanup handler."""
    assert not hasattr(ResidualAnalysisDialog, "_delete")
    assert not hasattr(ResidualAnalysisDialog, "_cleanup")
    assert not hasattr(ResidualAnalysisDialog, "_trash")
