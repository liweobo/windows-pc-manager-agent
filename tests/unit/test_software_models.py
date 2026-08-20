from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from pc_manager_agent.domain.software_uninstall_analysis import (
    CapabilitySupport,
    SoftwareIdentity,
    SoftwareSafetyAssessment,
    SoftwareSafetyClass,
    SoftwareSafetyDecision,
    SoftwareSource,
    SoftwareTargetQuery,
    SoftwareUninstallAnalysisPlan,
    SoftwareUninstallPreview,
    UninstallCapability,
    UninstallCapabilityType,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareArchitecture, SoftwareScope
from pc_manager_agent.orchestration.software_inventory import normalize_raw_entry
from tests.fixtures.software_analysis import msi_entry


def test_raw_commands_are_excluded_from_serialization_and_repr() -> None:
    raw = msi_entry()
    dumped = raw.model_dump(mode="json")
    assert "uninstall_string" not in dumped
    assert "quiet_uninstall_string" not in dumped
    assert "MsiExec" not in repr(raw)
    assert len(raw.command_metadata_digest()) == 64


def test_identity_is_source_qualified_and_architecture_distinct() -> None:
    x64 = normalize_raw_entry(msi_entry())
    x86 = normalize_raw_entry(msi_entry(architecture=SoftwareArchitecture.X86))
    assert x64 is not None and x86 is not None
    assert x64.identity.canonical_digest() != x86.identity.canonical_digest()
    assert x64.metadata_warnings


def test_target_query_rejects_unbounded_selection() -> None:
    with pytest.raises(ValidationError, match="identity digest or display name"):
        SoftwareTargetQuery()


def test_preview_rejects_any_execution_flag() -> None:
    item = normalize_raw_entry(msi_entry())
    assert item is not None
    plan = SoftwareUninstallAnalysisPlan(
        user_goal="卸载软件 Example App",
        summary="read only",
        target_query=SoftwareTargetQuery(display_name="Example App"),
        tool_names=("software.inventory",),
    )
    capability = UninstallCapability(
        capability_type=UninstallCapabilityType.MSI,
        support=CapabilitySupport.METADATA_SUPPORTED,
        confidence="high",
        evidence=("test",),
    )
    safety = SoftwareSafetyAssessment(
        safety_class=SoftwareSafetyClass.USER_APPLICATION,
        decision=SoftwareSafetyDecision.PREVIEW_ALLOWED,
        evidence=("test",),
        reasons=("test",),
    )
    now = datetime.now(UTC)
    from pc_manager_agent.domain.software_uninstall_analysis import SoftwareImpactAssessment

    with pytest.raises(ValidationError, match="never execute"):
        SoftwareUninstallPreview(
            plan_id=plan.plan_id,
            plan_digest=plan.canonical_digest(),
            generated_at=now,
            expires_at=now + timedelta(minutes=1),
            target=item,
            identity_digest=item.identity.canonical_digest(),
            metadata_digest=item.metadata_digest(),
            capability=capability,
            capability_digest=capability.canonical_digest(),
            safety=safety,
            impact=SoftwareImpactAssessment(
                findings=(),
                process_probe_complete=True,
                startup_probe_complete=True,
                service_probe_complete=True,
            ),
            blocked=False,
            stop_reason="stop",
            executable_in_current_stage=True,
        )


def test_identity_requires_valid_digest_length() -> None:
    with pytest.raises(ValidationError):
        SoftwareIdentity(
            source=SoftwareSource.UNKNOWN,
            scope=SoftwareScope.CURRENT_USER,
            architecture=SoftwareArchitecture.X64,
            display_name="Unknown",
            source_anchor_digest="bad",
        )
