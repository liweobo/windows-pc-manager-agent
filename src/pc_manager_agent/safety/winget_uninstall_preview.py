"""Preview construction for one exact winget package and software mapping."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pc_manager_agent.domain.software_uninstall_analysis import NormalizedInstalledSoftware
from pc_manager_agent.domain.winget_uninstall import (
    NormalizedWingetPackage,
    WingetAvailability,
    WingetCapabilityAssessment,
    WingetExecutionAssessment,
    WingetExecutionDecision,
    WingetExecutionPreflight,
    WingetPreflightState,
    WingetSoftwareMapping,
    WingetUninstallPlan,
    WingetUninstallPreview,
)


class WingetUninstallPreviewEngine:
    """Bind every safety fact into one expiring, object-specific Preview."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        if ttl_seconds <= 0:
            raise ValueError("winget Preview TTL must be positive")
        self._ttl_seconds = ttl_seconds

    def build(
        self,
        plan: WingetUninstallPlan,
        package: NormalizedWingetPackage,
        software: NormalizedInstalledSoftware,
        mapping: WingetSoftwareMapping,
        availability: WingetAvailability,
        capability: WingetCapabilityAssessment,
        assessment: WingetExecutionAssessment,
        preflight: WingetExecutionPreflight,
    ) -> WingetUninstallPreview:
        """Construct a Preview only when its digests reproduce the plan exactly."""
        executable = availability.executable
        expected = (
            package.identity.canonical_digest(),
            software.identity.canonical_digest(),
            mapping.canonical_digest(),
            capability.canonical_digest(),
            executable.invariant_digest() if executable is not None else None,
            assessment.canonical_digest(),
            preflight.canonical_digest(),
        )
        planned = (
            plan.package_identity_digest,
            plan.software_identity_digest,
            plan.mapping_digest,
            plan.capability_digest,
            plan.executable_identity_digest,
            plan.safety_digest,
            plan.preflight_digest,
        )
        if expected != planned:
            raise ValueError("winget plan digests do not match Preview evidence")
        generated = datetime.now(UTC)
        allowed = (
            mapping.executable
            and executable is not None
            and assessment.decision is WingetExecutionDecision.ALLOW
            and preflight.state is WingetPreflightState.READY
        )
        return WingetUninstallPreview(
            plan_id=plan.plan_id,
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_digest=plan.canonical_digest(),
            generated_at=generated,
            expires_at=generated + timedelta(seconds=self._ttl_seconds),
            package=package,
            software=software,
            mapping=mapping,
            availability=availability,
            capability=capability,
            execution_assessment=assessment,
            preflight=preflight,
            executable=allowed,
        )
