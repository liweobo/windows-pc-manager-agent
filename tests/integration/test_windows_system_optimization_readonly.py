from __future__ import annotations

import os

import pytest

from pc_manager_agent.domain.system_diagnostics import SystemCollector
from pc_manager_agent.platform_support.windows.system_optimization import (
    WindowsSystemOptimizationPlatform,
)
from pc_manager_agent.tools.manifest import CancellationToken


@pytest.mark.skipif(os.name != "nt", reason="Windows-only query adapter")
def test_real_windows_optimization_probe_is_bounded_and_read_only() -> None:
    platform = WindowsSystemOptimizationPlatform()
    token = CancellationToken()
    snapshot = platform.collect_system_snapshot(
        collectors=(SystemCollector.DISKS,),
        sample_count=2,
        sample_interval_seconds=0.1,
        max_processes=50,
        max_items=50,
        cancellation=token,
    )
    storage = platform.analyze_storage(
        authorized_roots=(),
        max_objects=20,
        timeout_seconds=2.0,
        minimum_large_file_bytes=1024**3,
        inactive_days=90,
        cancellation=token,
    )
    assert snapshot.outcomes
    assert storage.observations
    assert all(not hasattr(item, "execute") for item in storage.observations)
