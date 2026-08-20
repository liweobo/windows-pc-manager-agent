from __future__ import annotations

from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.orchestration.software_inventory import SoftwareInventoryService
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.tools.manifest import CancellationToken
from tests.fixtures.software_analysis import FakeSoftwareInventoryPlatform, msi_entry


def test_inventory_preserves_distinct_versions_and_reports_missing_name() -> None:
    platform = FakeSoftwareInventoryPlatform(
        (
            msi_entry(version="1.0"),
            msi_entry(version="2.0", product_code="{22345678-1234-1234-1234-1234567890AB}"),
            msi_entry(name="   ", product_code="{32345678-1234-1234-1234-1234567890AB}"),
        )
    )
    snapshot = SoftwareInventoryService(platform).collect(10, CancellationToken())
    assert len(snapshot.inventory.entries) == 2
    assert any("no usable display name" in warning for warning in snapshot.inventory.warnings)
    assert snapshot.inventory.source_counts == {"msi": 2}


def test_resolver_returns_ambiguity_until_exact_identity_selected() -> None:
    inventory = SoftwareInventoryService(
        FakeSoftwareInventoryPlatform(
            (
                msi_entry(version="1.0"),
                msi_entry(
                    version="2.0",
                    product_code="{22345678-1234-1234-1234-1234567890AB}",
                ),
            )
        )
    )
    resolver = SoftwareTargetResolver(inventory)
    resolved, _ = resolver.resolve(
        SoftwareTargetQuery(display_name="Example App"), 100, CancellationToken()
    )
    assert resolved.ambiguous
    assert len(resolved.candidates) == 2
    digest = resolved.candidates[0].identity.canonical_digest()
    exact, _ = resolver.resolve(
        SoftwareTargetQuery(identity_digest=digest), 100, CancellationToken()
    )
    assert exact.selected is not None
    assert not exact.ambiguous


def test_resolver_does_not_auto_select_substring_candidate() -> None:
    resolver = SoftwareTargetResolver(
        SoftwareInventoryService(FakeSoftwareInventoryPlatform((msi_entry(),)))
    )
    resolved, _ = resolver.resolve(
        SoftwareTargetQuery(display_name="Example"), 100, CancellationToken()
    )
    assert resolved.ambiguous
    assert len(resolved.candidates) == 1
