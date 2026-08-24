"""Mechanism-only policy for official current-user winget packages."""

from __future__ import annotations

from pc_manager_agent.domain.winget_uninstall import (
    NormalizedWingetPackage,
    WingetAvailability,
    WingetAvailabilityState,
    WingetCapabilityAssessment,
    WingetCapabilityDecision,
    WingetSoftwareMapping,
)


class WingetCapabilityPolicy:
    """Separate package mechanism support from application safety classification."""

    def assess(
        self,
        package: NormalizedWingetPackage,
        mapping: WingetSoftwareMapping,
        availability: WingetAvailability,
    ) -> WingetCapabilityAssessment:
        """Allow the mechanism only when every structural identity is executable."""
        reasons: list[str] = []
        executable_digest: str | None = None
        if availability.state is not WingetAvailabilityState.AVAILABLE:
            reasons.append(availability.reason)
        elif availability.executable is not None:
            executable_digest = availability.executable.invariant_digest()
        if not mapping.executable:
            reasons.append("Package-to-Software mapping is not singular high-confidence evidence.")
        return WingetCapabilityAssessment(
            decision=(
                WingetCapabilityDecision.SUPPORTED
                if not reasons
                else WingetCapabilityDecision.BLOCKED
            ),
            reasons=tuple(reasons) or ("Official current-user winget mechanism is available.",),
            package_identity_digest=package.identity.canonical_digest(),
            executable_identity_digest=executable_digest,
        )
