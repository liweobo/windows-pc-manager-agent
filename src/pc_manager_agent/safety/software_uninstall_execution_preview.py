"""Fresh execution Preview generation for Stage 4D2A."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pc_manager_agent.domain.software_uninstall_analysis import (
    NormalizedInstalledSoftware,
    UninstallCapability,
)
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiExecutionAssessment,
    MsiExecutionDecision,
    MsiPreflightState,
    MsiUninstallPlan,
    MsiUninstallPreview,
    SoftwareExecutionPreflightResult,
    ValidatedMsiProduct,
)


class MsiUninstallPreviewEngine:
    """Build an expiring Preview only from fresh deterministic local evidence."""

    def __init__(
        self,
        ttl_seconds: int = 300,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("MSI Preview TTL must be positive")
        self._ttl = ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))

    def build(
        self,
        plan: MsiUninstallPlan,
        target: NormalizedInstalledSoftware,
        product: ValidatedMsiProduct,
        capability: UninstallCapability,
        assessment: MsiExecutionAssessment,
        preflight: SoftwareExecutionPreflightResult,
    ) -> MsiUninstallPreview:
        """Bind one plan to its current identity, mechanism, policy, and preflight."""
        expected = (
            target.identity.canonical_digest(),
            product.evidence_digest(),
            capability.canonical_digest(),
            assessment.canonical_digest(),
            preflight.canonical_digest(),
            assessment.risk_level,
        )
        actual = (
            plan.identity_digest,
            plan.validated_product_digest,
            plan.capability_digest,
            plan.execution_assessment_digest,
            plan.preflight_digest,
            plan.risk_level,
        )
        if actual != expected:
            raise ValueError("MSI plan evidence changed before Preview generation")
        current = self._now()
        executable = (
            assessment.decision is MsiExecutionDecision.ALLOW
            and preflight.state is MsiPreflightState.READY
        )
        return MsiUninstallPreview(
            plan_id=plan.plan_id,
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_digest=plan.canonical_digest(),
            generated_at=current,
            expires_at=current + timedelta(seconds=self._ttl),
            target=target,
            identity_digest=target.identity.canonical_digest(),
            validated_product=product,
            capability=capability,
            execution_assessment=assessment,
            preflight=preflight,
            executable=executable,
        )
