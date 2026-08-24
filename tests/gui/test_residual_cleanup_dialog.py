"""GUI proof that Stage 4D4 keeps Fresh Preview and two confirmations visible."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot
from tests.fixtures.residual_cleanup import (
    build_residual_cleanup_environment,
    create_residual_report,
)
from tests.fixtures.software_residuals import residual_context

from pc_manager_agent.app.runtime import ResidualCleanupServices
from pc_manager_agent.domain.residual_cleanup import ResidualCleanupRequest
from pc_manager_agent.ui.residual_cleanup_dialog import ResidualCleanupDialog


@pytest.mark.gui
def test_cleanup_dialog_requires_plan_then_immediate_confirmation(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    (root / "app.bin").write_bytes(b"program")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    report = create_residual_report(environment, context)
    candidate = next(item for item in report.candidates if item.path == root)
    services = ResidualCleanupServices(
        registry=environment.registry,
        repository=environment.cleanup_repository,
        service=environment.service,
    )
    runtime = MagicMock()
    runtime.create_residual_cleanup_services.return_value = services
    dialog = ResidualCleanupDialog(
        runtime,
        ResidualCleanupRequest(
            source_report_id=report.report_id,
            selected_residual_ids=(candidate.candidate_id,),
        ),
    )
    qtbot.addWidget(dialog)
    try:
        qtbot.waitUntil(lambda: dialog._stage == "PLAN", timeout=10_000)
        assert dialog.cancel.isDefault()
        assert "第一次确认" in dialog.summary.toPlainText()
        assert environment.recycle.calls == []
        labels = {button.text() for button in dialog.findChildren(QPushButton)}
        assert not any("永久" in label for label in labels)

        dialog.primary.click()
        qtbot.waitUntil(lambda: dialog._stage == "RUNTIME", timeout=10_000)
        assert "第二次确认" in dialog.summary.toPlainText()
        assert "MANUAL" in dialog.summary.toPlainText()
        assert environment.recycle.calls == []

        dialog.primary.click()
        qtbot.waitUntil(lambda: dialog._stage == "DONE", timeout=10_000)
        assert len(environment.recycle.calls) == 1
        assert "成功验证：1" in dialog.summary.toPlainText()
        assert "没有永久删除" in dialog.summary.toPlainText()
    finally:
        dialog.close()
        environment.close()


@pytest.mark.gui
def test_cleanup_dialog_blocks_protected_selection_as_one_batch(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    root = tmp_path / "SyntheticProduct"
    root.mkdir()
    database = root / "user.db"
    database.write_bytes(b"protected")
    environment = build_residual_cleanup_environment(tmp_path / "state.db")
    context = residual_context(root)
    report = create_residual_report(environment, context)
    candidate = next(item for item in report.candidates if item.path == database)
    runtime = MagicMock()
    runtime.create_residual_cleanup_services.return_value = ResidualCleanupServices(
        registry=environment.registry,
        repository=environment.cleanup_repository,
        service=environment.service,
    )
    dialog = ResidualCleanupDialog(
        runtime,
        ResidualCleanupRequest(
            source_report_id=report.report_id,
            selected_residual_ids=(candidate.candidate_id,),
        ),
    )
    qtbot.addWidget(dialog)
    try:
        qtbot.waitUntil(lambda: dialog._stage == "BLOCKED", timeout=10_000)
        assert "没有执行任何清理" in dialog.summary.toPlainText()
        assert environment.recycle.calls == []
        assert database.exists()
    finally:
        dialog.close()
        environment.close()
