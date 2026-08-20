from __future__ import annotations

from pathlib import Path

from pc_manager_agent.domain.software_uninstall_analysis import (
    SoftwareSafetyClass,
    SoftwareSafetyDecision,
)
from pc_manager_agent.orchestration.software_impact_analyzer import SoftwareImpactAnalyzer
from pc_manager_agent.orchestration.software_inventory import normalize_raw_entry
from pc_manager_agent.safety.software_uninstall_policy import SoftwareUninstallSafetyPolicy
from pc_manager_agent.tools.manifest import CancellationToken
from tests.fixtures.software_analysis import msi_entry
from tests.fixtures.system_diagnostics import FakeSystemPlatform


def test_policy_allows_named_user_application_preview_only() -> None:
    item = normalize_raw_entry(msi_entry())
    assert item is not None
    assessment = SoftwareUninstallSafetyPolicy(Path("D:/Agent"), Path("C:/Windows")).assess(item)
    assert assessment.safety_class is SoftwareSafetyClass.USER_APPLICATION
    assert assessment.decision is SoftwareSafetyDecision.PREVIEW_ALLOWED


def test_policy_blocks_agent_security_driver_and_unknown() -> None:
    policy = SoftwareUninstallSafetyPolicy(Path("D:/Agent"), Path("C:/Windows"))
    agent = normalize_raw_entry(msi_entry(name="Windows PC Manager Agent"))
    security = normalize_raw_entry(msi_entry(name="Endpoint Protection Antivirus"))
    driver = normalize_raw_entry(msi_entry(name="Device Driver Package"))
    unknown = normalize_raw_entry(msi_entry(publisher=""))
    assert all(item is not None for item in (agent, security, driver, unknown))
    assert policy.assess(agent).safety_class is SoftwareSafetyClass.AGENT_COMPONENT  # type: ignore[arg-type]
    assert policy.assess(security).safety_class is SoftwareSafetyClass.SECURITY_SOFTWARE  # type: ignore[arg-type]
    assert policy.assess(driver).safety_class is SoftwareSafetyClass.DEVICE_DRIVER  # type: ignore[arg-type]
    assert policy.assess(unknown).decision is SoftwareSafetyDecision.BLOCKED  # type: ignore[arg-type]


def test_impact_analysis_labels_known_and_heuristic_findings() -> None:
    item = normalize_raw_entry(msi_entry())
    assert item is not None
    policy = SoftwareUninstallSafetyPolicy(Path("D:/Agent"), Path("C:/Windows"))
    impact = SoftwareImpactAnalyzer(FakeSystemPlatform()).analyze(
        item, policy.assess(item), CancellationToken()
    )
    codes = {finding.code for finding in impact.findings}
    assert {"estimated-size", "running-processes", "startup-references"} <= codes
    assert "service-references" in codes
    assert "residual-user-data" in codes
    assert impact.process_probe_complete
    assert "does not build a complete" in impact.disclaimer
