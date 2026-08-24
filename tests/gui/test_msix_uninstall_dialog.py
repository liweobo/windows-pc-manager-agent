"""GUI contract tests for explicit MSIX data-impact confirmation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pc_manager_agent.domain.msix_uninstall import MsixTargetQuery
from pc_manager_agent.ui.msix_uninstall_dialog import MsixUninstallDialog


@pytest.mark.gui
def test_msix_dialog_defaults_to_cancel_and_disables_execution(qtbot: object) -> None:
    """Opening the dialog cannot dispatch before asynchronous evidence is ready."""
    runtime = MagicMock()
    dialog = MsixUninstallDialog(
        runtime,
        "remove example",
        query=MsixTargetQuery(full_name="Example.App_1.0.0.0_x64__publisher"),
    )
    qtbot.addWidget(dialog)  # type: ignore[attr-defined]
    assert dialog.primary.isEnabled() is False
    assert dialog.cancel.isDefault() is True
    dialog._failed("test stop")
    dialog.close()


def test_msix_dialog_source_discloses_approved_data_semantics() -> None:
    """The UI explicitly names roaming preservation, LocalState, and no extra deletion."""
    import inspect

    source = inspect.getsource(MsixUninstallDialog)
    assert "Roamable" in source
    assert "LocalState" in source
    assert "不额外删除" in source
    assert "不运行 PowerShell" in source
    assert "分析可能残留" in source
