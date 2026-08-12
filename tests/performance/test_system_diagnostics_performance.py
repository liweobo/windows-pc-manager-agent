from __future__ import annotations

import time
import tracemalloc

import pytest

from pc_manager_agent.domain.system_diagnostics import (
    InstalledSoftware,
    SoftwareArchitecture,
    SoftwareScope,
)


@pytest.mark.performance
def test_large_software_inventory_validation_is_bounded() -> None:
    started = time.perf_counter()
    tracemalloc.start()
    records = tuple(
        InstalledSoftware(
            name=f"Application {index}",
            version="1.0",
            publisher="Publisher",
            scope=SoftwareScope.CURRENT_USER,
            architecture=SoftwareArchitecture.X64,
            registry_key=f"SOFTWARE/Uninstall/{index}",
        )
        for index in range(5_000)
    )
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(records) == 5_000
    assert elapsed < 10
    assert peak < 128 * 1_048_576
