"""GUI proof of default-unchecked selection and two separate Stage 4E2 approvals."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot
from tests.fixtures.system_cleanup import (
    build_system_cleanup_environment,
    mark_old,
    save_temp_report,
)

from pc_manager_agent.app.runtime import SystemCleanupServices
from pc_manager_agent.domain.system_cleanup_execution import SystemCleanupRequest
from pc_manager_agent.ui.system_cleanup_dialog import (
    RecycleBinEmptyDialog,
    SystemCleanupDialog,
)


@pytest.mark.gui
def test_item_cleanup_defaults_unchecked_then_requires_two_confirmations(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    target = environment.temp_root / "old.tmp"
    target.write_bytes(b"old")
    mark_old(target)
    report = save_temp_report(environment)
    runtime = MagicMock()
    runtime.create_system_cleanup_services.return_value = SystemCleanupServices(
        registry=environment.registry,
        repository=environment.repository,
        service=environment.service,
    )
    dialog = SystemCleanupDialog(
        runtime,
        SystemCleanupRequest(
            source_report_id=report.report_id,
            selected_candidate_ids=(report.cleanup_candidates[0].candidate_id,),
        ),
    )
    qtbot.addWidget(dialog)
    try:
        qtbot.waitUntil(lambda: dialog._stage == "SELECTION", timeout=10_000)
        selector = dialog.table.item(0, 0)
        assert selector.checkState() is Qt.CheckState.Unchecked
        assert not dialog.primary.isEnabled()
        assert dialog.cancel.isDefault()
        labels = {button.text() for button in dialog.findChildren(QPushButton)}
        assert not any("全选" in label or "永久删除" in label for label in labels)
        assert environment.recycle.calls == []

        selector.setCheckState(Qt.CheckState.Checked)
        assert dialog.primary.isEnabled()
        dialog.primary.click()
        assert dialog._stage == "PLAN"
        assert "计划确认" in dialog.summary.toPlainText()
        assert "MANUAL" in dialog.summary.toPlainText()
        assert environment.recycle.calls == []

        dialog.primary.click()
        qtbot.waitUntil(lambda: dialog._stage == "RUNTIME", timeout=10_000)
        assert "第二次确认" in dialog.summary.toPlainText()
        assert environment.recycle.calls == []

        dialog.primary.click()
        qtbot.waitUntil(lambda: dialog._stage == "DONE", timeout=10_000)
        assert environment.recycle.calls == [target]
        assert "已验证释放磁盘空间：未知" in dialog.summary.toPlainText()
        assert "MANUAL" in dialog.summary.toPlainText()
    finally:
        dialog.close()
        environment.close()


@pytest.mark.gui
def test_recycle_bin_empty_has_its_own_none_recovery_confirmations(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    environment = build_system_cleanup_environment(tmp_path / "state.db")
    runtime = MagicMock()
    runtime.create_system_cleanup_services.return_value = SystemCleanupServices(
        registry=environment.registry,
        repository=environment.repository,
        service=environment.service,
    )
    dialog = RecycleBinEmptyDialog(runtime)
    qtbot.addWidget(dialog)
    try:
        qtbot.waitUntil(lambda: dialog._stage == "PLAN", timeout=10_000)
        assert "第一次确认" in dialog.summary.toPlainText()
        assert "NONE" in dialog.summary.toPlainText()
        assert environment.empty_platform.empty_calls == []

        dialog.primary.click()
        qtbot.waitUntil(lambda: dialog._stage == "RUNTIME", timeout=10_000)
        assert "第二次确认" in dialog.summary.toPlainText()
        assert "NONE" in dialog.summary.toPlainText()
        assert environment.empty_platform.empty_calls == []

        dialog.primary.click()
        qtbot.waitUntil(lambda: dialog._stage == "DONE", timeout=10_000)
        assert len(environment.empty_platform.empty_calls) == 1
        assert "恢复等级始终为 NONE" in dialog.summary.toPlainText()
    finally:
        dialog.close()
        environment.close()
