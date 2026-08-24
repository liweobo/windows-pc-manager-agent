"""GUI safety defaults for the Stage 4D2C1 winget dialog."""

from __future__ import annotations

from typing import Any

from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.ui.winget_uninstall_dialog import WingetUninstallDialog


def test_dialog_keeps_cancel_as_default_and_execution_disabled(
    qtbot: Any,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(WingetUninstallDialog, "_start_prepare", lambda self: None)
    dialog = WingetUninstallDialog(
        object(),
        "卸载 Example",
        query=SoftwareTargetQuery(display_name="Example"),
    )
    qtbot.addWidget(dialog)
    assert dialog.cancel_button.isDefault()
    assert not dialog.primary_button.isEnabled()
    assert "R2" in dialog.risk_label.text()
    assert "无法自动撤销" in dialog.risk_label.text()
