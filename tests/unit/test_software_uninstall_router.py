"""Unit coverage for deterministic MSI/Vendor mechanism routing."""

from __future__ import annotations

from pathlib import Path

from pc_manager_agent.domain.software_uninstall_analysis import (
    RawInstalledSoftwareEntry,
    SoftwareTargetQuery,
)
from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
from pc_manager_agent.orchestration.software_inventory import SoftwareInventoryService
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.orchestration.software_uninstall_router import (
    SoftwareUninstallMechanism,
    SoftwareUninstallRouter,
)
from tests.fixtures.software_analysis import FakeSoftwareInventoryPlatform, msi_entry
from tests.fixtures.vendor_uninstall import vendor_entry


def _router(*entries: RawInstalledSoftwareEntry) -> SoftwareUninstallRouter:
    inventory = SoftwareInventoryService(FakeSoftwareInventoryPlatform(tuple(entries)))
    return SoftwareUninstallRouter(
        SoftwareTargetResolver(inventory),
        UninstallCapabilityResolver(),
    )


def test_router_selects_msi_only_from_structured_msi_metadata() -> None:
    route = _router(msi_entry()).route(SoftwareTargetQuery(display_name="Example App"))
    assert route.mechanism is SoftwareUninstallMechanism.MSI


def test_router_selects_interactive_vendor_and_ignores_quiet_preference(
    tmp_path: Path,
) -> None:
    install = tmp_path / "app"
    install.mkdir()
    (install / "uninstall.exe").write_bytes(b"fixture")
    route = _router(vendor_entry(install)).route(SoftwareTargetQuery(display_name="Example App"))
    assert route.mechanism is SoftwareUninstallMechanism.VENDOR


def test_router_never_routes_quiet_only_metadata(tmp_path: Path) -> None:
    install = tmp_path / "app"
    install.mkdir()
    (install / "uninstall.exe").write_bytes(b"fixture")
    entry = vendor_entry(install).model_copy(update={"uninstall_string": None})
    route = _router(entry).route(SoftwareTargetQuery(display_name="Example App"))
    assert route.mechanism is SoftwareUninstallMechanism.UNSUPPORTED


def test_router_keeps_duplicate_display_names_ambiguous(tmp_path: Path) -> None:
    install = tmp_path / "app"
    install.mkdir()
    (install / "uninstall.exe").write_bytes(b"fixture")
    first = vendor_entry(install, version="1.0")
    second = vendor_entry(install, version="2.0").model_copy(
        update={"raw_source_id": r"HKCU\Software\Example2"}
    )
    route = _router(first, second).route(SoftwareTargetQuery(display_name="Example App"))
    assert route.mechanism is SoftwareUninstallMechanism.AMBIGUOUS
    assert route.resolution.ambiguous
