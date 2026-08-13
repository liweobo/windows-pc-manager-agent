"""Real Windows Stage 4B adapter probe that performs inventory and DPAPI reads only."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import pywintypes

from pc_manager_agent.domain.startup_actions import StartupManagementMode, StartupSource
from pc_manager_agent.platform_support.windows.data_protection import (
    WindowsCurrentUserDataProtector,
)
from pc_manager_agent.platform_support.windows.startup_management import (
    WindowsStartupManagementPlatform,
    _publisher,
)


@pytest.mark.windows
@pytest.mark.skipif(os.name != "nt", reason="Windows-only read-only adapter probe")
def test_real_startup_inventory_and_dpapi_are_read_only(tmp_path: Path) -> None:
    platform = WindowsStartupManagementPlatform(tmp_path / "disabled-startup")
    entries = platform.list_entries(100)

    assert len(entries) <= 100
    assert all(entry.identity.source in set(StartupSource) for entry in entries)
    assert all(
        entry.management_mode is not StartupManagementMode.DISABLE_SUPPORTED
        or (
            entry.scope == "CURRENT_USER"
            and entry.identity.source in {StartupSource.HKCU_RUN, StartupSource.USER_STARTUP_FOLDER}
        )
        for entry in entries
    )
    protector = WindowsCurrentUserDataProtector()
    ciphertext = protector.protect(b"stage4b-read-only-probe")
    assert protector.unprotect(ciphertext) == b"stage4b-read-only-probe"


def test_missing_windows_version_resource_keeps_publisher_unknown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A valid executable without version metadata must not abort inventory."""

    def no_version_resource(*_args: object) -> Any:
        raise pywintypes.error(1813, "GetFileVersionInfo", "resource not found")

    monkeypatch.setattr("win32api.GetFileVersionInfo", no_version_resource)

    assert _publisher(tmp_path / "no-version.exe") is None
