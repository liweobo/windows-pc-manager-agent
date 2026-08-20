from __future__ import annotations

import time

import pytest
from tests.fixtures.software_analysis import FakeSoftwareInventoryPlatform, msi_entry

from pc_manager_agent.orchestration.software_inventory import SoftwareInventoryService
from pc_manager_agent.tools.manifest import CancellationToken


@pytest.mark.performance
def test_normalizes_ten_thousand_synthetic_entries_within_bound() -> None:
    entries = tuple(
        msi_entry(
            name=f"Synthetic App {index}",
            product_code=f"{{{index:08X}-1234-1234-1234-{index:012X}}}",
        )
        for index in range(10_000)
    )
    service = SoftwareInventoryService(FakeSoftwareInventoryPlatform(entries))
    started = time.perf_counter()
    snapshot = service.collect(10_000, CancellationToken())
    duration = time.perf_counter() - started
    assert len(snapshot.inventory.entries) == 10_000
    assert duration < 10.0
