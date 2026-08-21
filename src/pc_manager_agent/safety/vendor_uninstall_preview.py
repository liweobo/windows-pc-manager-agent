"""Fresh expiring Preview generation for Stage 4D2B Vendor uninstall."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pc_manager_agent.domain.software_uninstall_analysis import (
    NormalizedInstalledSoftware,
    UninstallCapability,
)
from pc_manager_agent.domain.vendor_uninstall import (
    VendorExecutionAssessment,
    VendorExecutionPreflightResult,
    VendorUninstallerIdentity,
    VendorUninstallPlan,
    VendorUninstallPreview,
)


class VendorUninstallPreviewEngine:
    """Bind exact software, executable, arguments, policy, and preflight evidence."""

    def __init__(
        self,
        ttl_seconds: int = 300,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Vendor Preview TTL must be positive")
        self._ttl = ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))

    def build(
        self,
        plan: VendorUninstallPlan,
        target: NormalizedInstalledSoftware,
        capability: UninstallCapability,
        identity: VendorUninstallerIdentity,
        assessment: VendorExecutionAssessment,
        preflight: VendorExecutionPreflightResult,
    ) -> VendorUninstallPreview:
        """Build one Preview only when all immutable plan evidence still matches."""
        expected = (
            target.identity.canonical_digest(),
            capability.canonical_digest(),
            identity.invariant_digest(),
            assessment.canonical_digest(),
            preflight.canonical_digest(),
            assessment.risk_level,
        )
        actual = (
            plan.identity_digest,
            plan.capability_digest,
            plan.vendor_identity_digest,
            plan.execution_assessment_digest,
            plan.preflight_digest,
            plan.risk_level,
        )
        if actual != expected:
            raise ValueError("Vendor plan evidence changed before Preview generation")
        current = self._now()
        executable = (
            identity.trust.decision.value == "trusted_for_execution"
            and identity.argument_assessment.decision.value == "allow"
            and assessment.decision.value == "allow"
            and preflight.state.value == "ready"
        )
        return VendorUninstallPreview(
            plan_id=plan.plan_id,
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_digest=plan.canonical_digest(),
            generated_at=current,
            expires_at=current + timedelta(seconds=self._ttl),
            target=target,
            identity_digest=target.identity.canonical_digest(),
            capability=capability,
            vendor_identity=identity,
            execution_assessment=assessment,
            preflight=preflight,
            executable=executable,
        )
