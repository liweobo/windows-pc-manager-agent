from __future__ import annotations

from pathlib import Path

import pytest
from tests.fixtures.software_analysis import msi_entry

from pc_manager_agent.domain.software_uninstall_analysis import SoftwareSafetyDecision
from pc_manager_agent.orchestration.software_inventory import normalize_raw_entry
from pc_manager_agent.safety.software_uninstall_policy import SoftwareUninstallSafetyPolicy


@pytest.mark.security
@pytest.mark.parametrize(
    "name,publisher",
    (
        ("Endpoint Protection Antivirus", "Security Vendor"),
        ("Device Driver Package", "Hardware Vendor"),
        ("Windows Component", "Microsoft Corporation"),
        ("Enterprise Management Agent", "Company IT"),
    ),
)
def test_protected_software_classes_are_blocked(name: str, publisher: str) -> None:
    item = normalize_raw_entry(msi_entry(name=name, publisher=publisher))
    assert item is not None
    policy = SoftwareUninstallSafetyPolicy(Path("D:/Agent"), Path("C:/Windows"))
    assert policy.assess(item).decision is SoftwareSafetyDecision.BLOCKED


@pytest.mark.security
def test_agent_install_location_is_always_blocked() -> None:
    item = normalize_raw_entry(msi_entry())
    assert item is not None
    policy = SoftwareUninstallSafetyPolicy(Path("C:/Apps"), Path("C:/Windows"))
    assert policy.assess(item).decision is SoftwareSafetyDecision.BLOCKED


@pytest.mark.security
@pytest.mark.parametrize(
    "name,expected_decision",
    (
        ("winget Package Manager", SoftwareSafetyDecision.PREVIEW_HIGH_IMPACT),
        ("Example VPN Client", SoftwareSafetyDecision.PREVIEW_HIGH_IMPACT),
        ("Postgres Database", SoftwareSafetyDecision.PREVIEW_HIGH_IMPACT),
        ("Docker Desktop", SoftwareSafetyDecision.PREVIEW_HIGH_IMPACT),
        ("Visual C++ Runtime", SoftwareSafetyDecision.PREVIEW_HIGH_IMPACT),
        ("NVIDIA Control Utility", SoftwareSafetyDecision.PREVIEW_HIGH_IMPACT),
        ("Visual Studio IDE", SoftwareSafetyDecision.PREVIEW_ALLOWED),
    ),
)
def test_high_impact_and_developer_classes_are_preview_only(
    name: str, expected_decision: SoftwareSafetyDecision
) -> None:
    item = normalize_raw_entry(msi_entry(name=name))
    assert item is not None
    policy = SoftwareUninstallSafetyPolicy(Path("D:/Agent"), Path("C:/Windows"))
    assert policy.assess(item).decision is expected_decision
