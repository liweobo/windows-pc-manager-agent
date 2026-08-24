"""Read-only Windows contract test for the structured current-user PackageManager adapter."""

from __future__ import annotations

import sys

import pytest

from pc_manager_agent.domain.msix_uninstall import MsixInventoryState, MsixScope
from pc_manager_agent.platform_support.windows.msix_packages import WindowsMsixPackagePlatform
from pc_manager_agent.tools.manifest import CancellationToken


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows WinRT contract")
def test_real_msix_inventory_is_current_user_and_read_only() -> None:
    """Read a bounded inventory without printing identities or calling removal APIs."""
    inventory = WindowsMsixPackagePlatform().inventory_current_user(500, CancellationToken())
    assert inventory.state in {MsixInventoryState.COMPLETE, MsixInventoryState.TRUNCATED}
    assert all(item.identity.scope is MsixScope.CURRENT_USER for item in inventory.packages)
    assert all(item.identity.provisioned_state == "not_queried" for item in inventory.packages)
