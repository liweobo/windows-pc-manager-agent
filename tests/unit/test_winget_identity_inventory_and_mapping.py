"""Unit coverage for winget identity, inventory, resolver, mapping, and policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareSafetyClass
from pc_manager_agent.domain.system_diagnostics import SoftwareScope
from pc_manager_agent.domain.winget_uninstall import (
    OFFICIAL_WINGET_SOURCE_IDENTIFIER,
    WingetAvailability,
    WingetAvailabilityState,
    WingetCapabilityDecision,
    WingetExecutionDecision,
    WingetInventoryState,
    WingetMappingConfidence,
    WingetPackageIdentity,
    WingetPackageQuery,
    fixed_winget_uninstall_arguments,
)
from pc_manager_agent.orchestration.software_inventory import normalize_raw_entry
from pc_manager_agent.orchestration.winget_software_mapping import WingetSoftwareMapper
from pc_manager_agent.orchestration.winget_target_resolver import PackageTargetResolver
from pc_manager_agent.platform_support.windows.winget_uninstall import _parse_export
from pc_manager_agent.safety.winget_capability_policy import WingetCapabilityPolicy
from pc_manager_agent.safety.winget_uninstall_policy import WingetUninstallPolicy
from tests.fixtures.winget_uninstall import executable_identity, package, winget_entry


def test_package_identity_rejects_injection_source_and_machine_scope() -> None:
    with pytest.raises(ValueError, match="characters"):
        WingetPackageIdentity(
            package_id="Example.App & calc.exe",
            installed_version="1.0",
            source_name="winget",
            source_identifier=OFFICIAL_WINGET_SOURCE_IDENTIFIER,
            scope=SoftwareScope.CURRENT_USER,
        )
    with pytest.raises(ValueError, match="official"):
        WingetPackageIdentity(
            package_id="Example.App",
            installed_version="1.0",
            source_name="custom",
            source_identifier="custom",
            scope=SoftwareScope.CURRENT_USER,
        )
    with pytest.raises(ValueError, match="current-user"):
        WingetPackageIdentity(
            package_id="Example.App",
            installed_version="1.0",
            source_name="winget",
            source_identifier=OFFICIAL_WINGET_SOURCE_IDENTIFIER,
            scope=SoftwareScope.LOCAL_MACHINE,
        )


def test_fixed_arguments_have_no_override_silent_force_or_restart() -> None:
    identity = package().identity
    arguments = fixed_winget_uninstall_arguments(identity)
    assert arguments == (
        "uninstall",
        "--id",
        "Example.CleanApp",
        "--exact",
        "--source",
        "winget",
        "--version",
        "1.0.0",
        "--scope",
        "user",
        "--interactive",
        "--disable-interactivity",
    )
    assert not {"--override", "--silent", "--force", "--all", "--purge"} & set(arguments)


def test_export_parser_keeps_only_official_source_and_bounds_results(tmp_path: Path) -> None:
    output = tmp_path / "packages.json"
    output.write_text(
        json.dumps(
            {
                "Sources": [
                    {
                        "SourceDetails": {
                            "Name": "winget",
                            "Identifier": OFFICIAL_WINGET_SOURCE_IDENTIFIER,
                            "Argument": "https://not-persisted.example",
                        },
                        "Packages": [
                            {"PackageIdentifier": "Example.One", "Version": "1.0"},
                            {"PackageIdentifier": "Example.Two", "Version": "2.0"},
                        ],
                    },
                    {
                        "SourceDetails": {"Name": "custom", "Identifier": "evil"},
                        "Packages": [{"PackageIdentifier": "Bad.Package", "Version": "1.0"}],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    inventory = _parse_export(output, 1)
    assert inventory.state is WingetInventoryState.TRUNCATED
    assert [item.package_id for item in inventory.packages] == ["Example.One"]
    assert all("non-official" not in warning for warning in inventory.warnings)
    assert "not-persisted" not in inventory.model_dump_json()


def test_target_resolver_never_uses_package_name_or_auto_selects_versions() -> None:
    inventory = package()
    from pc_manager_agent.domain.winget_uninstall import WingetPackageInventory

    snapshot = WingetPackageInventory(
        state=WingetInventoryState.COMPLETE,
        packages=(inventory, package(version="2.0")),
    )
    result = PackageTargetResolver.resolve_from_inventory(
        WingetPackageQuery(package_id="Example.CleanApp"),
        snapshot,
    )
    assert result.ambiguous
    assert result.selected is None


def test_mapping_requires_structured_current_user_id_version_link(tmp_path: Path) -> None:
    raw = winget_entry(tmp_path)
    software = normalize_raw_entry(raw)
    assert software is not None
    mapping, selected = WingetSoftwareMapper().map(package(), (software,))
    assert mapping.confidence is WingetMappingConfidence.HIGH
    assert selected == software

    changed = software.model_copy(update={"display_version": "2.0"})
    mapping, selected = WingetSoftwareMapper().map(package(), (changed,))
    assert mapping.confidence is not WingetMappingConfidence.HIGH
    assert selected is None

    duplicate = normalize_raw_entry(winget_entry(tmp_path / "duplicate"))
    assert duplicate is not None
    mapping, selected = WingetSoftwareMapper().map(package(), (software, duplicate))
    assert mapping.confidence is WingetMappingConfidence.NONE
    assert selected is None

    heuristic = software.model_copy(
        update={
            "display_name": "Example.CleanApp",
            "identity": software.identity.model_copy(
                update={"package_id": None, "package_manager_id": None}
            ),
        }
    )
    mapping, selected = WingetSoftwareMapper().map(package(), (heuristic,))
    assert mapping.confidence is WingetMappingConfidence.MEDIUM
    assert selected is None

    mapping, selected = WingetSoftwareMapper().map(package(), ())
    assert mapping.confidence is WingetMappingConfidence.NONE
    assert selected is None


def test_capability_requires_trusted_alias_and_high_confidence_mapping(tmp_path: Path) -> None:
    software = normalize_raw_entry(winget_entry(tmp_path))
    assert software is not None
    mapping, selected = WingetSoftwareMapper().map(package(), (software,))
    assert selected is not None
    available = WingetAvailability(
        state=WingetAvailabilityState.AVAILABLE,
        executable=executable_identity(tmp_path / "WindowsApps" / "winget.exe"),
        reason="trusted",
    )
    supported = WingetCapabilityPolicy().assess(package(), mapping, available)
    assert supported.decision is WingetCapabilityDecision.SUPPORTED

    unavailable = WingetAvailability(
        state=WingetAvailabilityState.UNAVAILABLE,
        reason="not installed",
    )
    blocked = WingetCapabilityPolicy().assess(package(), mapping, unavailable)
    assert blocked.decision is WingetCapabilityDecision.BLOCKED
    assert blocked.executable_identity_digest is None

    no_mapping, _ = WingetSoftwareMapper().map(package(), ())
    blocked = WingetCapabilityPolicy().assess(package(), no_mapping, available)
    assert blocked.decision is WingetCapabilityDecision.BLOCKED


@pytest.mark.parametrize(
    ("safety_class", "decision", "risk"),
    (
        (SoftwareSafetyClass.USER_APPLICATION, WingetExecutionDecision.ALLOW, RiskLevel.R2),
        (
            SoftwareSafetyClass.DEVELOPER_RUNTIME,
            WingetExecutionDecision.ALLOW,
            RiskLevel.R2_HIGH_IMPACT,
        ),
        (SoftwareSafetyClass.SHARED_RUNTIME, WingetExecutionDecision.BLOCK, RiskLevel.R2),
        (SoftwareSafetyClass.SECURITY_SOFTWARE, WingetExecutionDecision.BLOCK, RiskLevel.R2),
        (SoftwareSafetyClass.AGENT_COMPONENT, WingetExecutionDecision.BLOCK, RiskLevel.R2),
    ),
)
def test_execution_policy_keeps_protected_classes_blocked(
    safety_class: SoftwareSafetyClass,
    decision: WingetExecutionDecision,
    risk: RiskLevel,
) -> None:
    from pc_manager_agent.domain.software_uninstall_analysis import (
        SoftwareSafetyAssessment,
        SoftwareSafetyDecision,
    )

    assessment = SoftwareSafetyAssessment(
        safety_class=safety_class,
        decision=SoftwareSafetyDecision.PREVIEW_ALLOWED,
        evidence=("synthetic",),
        reasons=("synthetic",),
    )
    result = WingetUninstallPolicy().assess(SoftwareScope.CURRENT_USER, assessment)
    assert result.decision is decision
    assert result.risk_level is risk


def test_execution_policy_blocks_machine_scope_even_for_user_application() -> None:
    from pc_manager_agent.domain.software_uninstall_analysis import (
        SoftwareSafetyAssessment,
        SoftwareSafetyDecision,
    )

    assessment = SoftwareSafetyAssessment(
        safety_class=SoftwareSafetyClass.USER_APPLICATION,
        decision=SoftwareSafetyDecision.PREVIEW_ALLOWED,
        evidence=("synthetic",),
        reasons=("synthetic",),
    )
    result = WingetUninstallPolicy().assess(SoftwareScope.LOCAL_MACHINE, assessment)
    assert result.decision is WingetExecutionDecision.BLOCK
