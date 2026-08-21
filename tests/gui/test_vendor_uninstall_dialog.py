"""GUI state coverage for the two-confirmation Vendor workflow."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.fixtures.vendor_uninstall import build_vendor_environment

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.ui.vendor_uninstall_dialog import VendorUninstallDialog


@pytest.mark.gui
def test_vendor_dialog_keeps_cancel_default_and_hides_full_command(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = build_vendor_environment(tmp_path / "state.sqlite3")
    monkeypatch.setattr(
        runtime,
        "create_vendor_uninstall_services",
        lambda: environment.services,
    )
    dialog = VendorUninstallDialog(
        runtime,
        "卸载软件 Example App",
        query=SoftwareTargetQuery(display_name="Example App"),
    )
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog._stage == "PLAN_CONFIRMATION", timeout=10_000)
    assert dialog.cancel_button.isDefault()
    assert "回滚 NONE" in dialog.details.toPlainText()
    assert "uninstall.exe" not in dialog.details.toPlainText().casefold()
    assert "/remove" not in dialog.details.toPlainText().casefold()
    dialog.primary_button.click()
    qtbot.waitUntil(lambda: dialog._stage == "RUNTIME_CONFIRMATION", timeout=10_000)
    assert dialog.cancel_button.isDefault()
    assert "执行前即时确认" in dialog.details.toPlainText()
    dialog.primary_button.click()
    qtbot.waitUntil(lambda: dialog._stage == "COMPLETED", timeout=10_000)
    assert "verified_removed" in dialog.details.toPlainText()
    assert len(environment.adapter.calls) == 1
    environment.close()


@pytest.mark.gui
def test_vendor_dialog_rejects_plan_without_launching(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = build_vendor_environment(tmp_path / "state.sqlite3")
    monkeypatch.setattr(
        runtime,
        "create_vendor_uninstall_services",
        lambda: environment.services,
    )
    dialog = VendorUninstallDialog(
        runtime,
        "卸载软件 Example App",
        query=SoftwareTargetQuery(display_name="Example App"),
    )
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog._stage == "PLAN_CONFIRMATION", timeout=10_000)
    dialog.cancel_button.click()
    assert environment.adapter.calls == []
    environment.close()
