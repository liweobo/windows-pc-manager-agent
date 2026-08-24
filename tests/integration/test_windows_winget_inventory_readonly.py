"""Optional real-Windows read-only App Installer and package inventory probe."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pc_manager_agent.domain.winget_uninstall import (
    DESKTOP_APP_INSTALLER_FAMILY,
    WingetAvailabilityState,
    WingetInventoryState,
)
from pc_manager_agent.platform_support.windows.winget_uninstall import (
    WindowsWingetAvailabilityPlatform,
    WindowsWingetPackageInventoryPlatform,
)
from pc_manager_agent.tools.manifest import CancellationToken


@pytest.mark.skipif(sys.platform != "win32", reason="Windows App Installer probe")
def test_real_winget_probe_is_read_only_and_fail_closed(tmp_path: Path) -> None:
    availability_platform = WindowsWingetAvailabilityPlatform()
    availability = availability_platform.inspect()
    assert availability.state in {
        WingetAvailabilityState.AVAILABLE,
        WingetAvailabilityState.UNAVAILABLE,
        WingetAvailabilityState.UNTRUSTED,
    }
    if availability.executable is not None:
        assert availability.executable.package_family_name == DESKTOP_APP_INSTALLER_FAMILY
    inventory = WindowsWingetPackageInventoryPlatform(
        availability_platform,
        tmp_path,
        timeout_seconds=30,
    ).inventory(100, CancellationToken())
    assert inventory.state in {
        WingetInventoryState.COMPLETE,
        WingetInventoryState.FAILED,
        WingetInventoryState.TRUNCATED,
    }
