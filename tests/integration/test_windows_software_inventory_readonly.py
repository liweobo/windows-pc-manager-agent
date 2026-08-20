from __future__ import annotations

import os

import pytest

from pc_manager_agent.platform_support.windows.software_inventory import (
    WindowsSoftwareInventoryPlatform,
)
from pc_manager_agent.tools.manifest import CancellationToken


@pytest.mark.windows
@pytest.mark.skipif(os.name != "nt", reason="Windows-only registry inventory")
def test_real_windows_software_inventory_is_bounded_and_read_only() -> None:
    entries, warnings, truncated = WindowsSoftwareInventoryPlatform().collect_raw(
        100, CancellationToken()
    )
    assert len(entries) <= 100
    assert all(entry.raw_source_id for entry in entries)
    assert isinstance(warnings, tuple)
    assert isinstance(truncated, bool)
