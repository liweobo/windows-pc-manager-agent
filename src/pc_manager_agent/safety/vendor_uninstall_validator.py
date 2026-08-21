"""Independent semantic review of Stage 4D2B plans and Previews."""

from __future__ import annotations

from dataclasses import dataclass

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.vendor_uninstall import (
    VendorExecutionDecision,
    VendorPreflightState,
    VendorTrustDecision,
    VendorUninstallPlan,
    VendorUninstallPreview,
)
from pc_manager_agent.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class VendorUninstallSafetyReview:
    """Independent approval result with non-sensitive reasons."""

    approved: bool
    issues: tuple[str, ...]


class VendorUninstallSafetyValidator:
    """Reject generalized registries, stale evidence, or weakened irreversible gates."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def review(
        self,
        plan: VendorUninstallPlan,
        preview: VendorUninstallPreview,
    ) -> VendorUninstallSafetyReview:
        """Validate manifest semantics and every immutable evidence digest."""
        issues: list[str] = []
        if self._registry.names != ("software.uninstall.vendor",):
            return VendorUninstallSafetyReview(
                False,
                ("Stage 4D2B registry must contain exactly one Vendor uninstall tool.",),
            )
        manifest = self._registry.manifest("software.uninstall.vendor")
        if (
            manifest.read_only
            or manifest.risk_level is not RiskLevel.R2
            or manifest.rollback_level is not RollbackLevel.NONE
            or not manifest.irreversible
            or not manifest.requires_runtime_confirmation
            or not manifest.supports_preview
            or manifest.max_batch_size != 1
        ):
            issues.append("Vendor manifest weakens the irreversible R2 boundary.")
        if preview.plan_id != plan.plan_id or preview.plan_digest != plan.canonical_digest():
            issues.append("Preview does not match the immutable plan.")
        if preview.transaction_id != plan.transaction_id:
            issues.append("Preview transaction does not match the plan.")
        if preview.identity_digest != plan.identity_digest:
            issues.append("Software identity changed after planning.")
        if preview.capability.canonical_digest() != plan.capability_digest:
            issues.append("Vendor capability changed after planning.")
        if preview.vendor_identity.invariant_digest() != plan.vendor_identity_digest:
            issues.append("Executable identity or arguments changed after planning.")
        if preview.execution_assessment.canonical_digest() != plan.execution_assessment_digest:
            issues.append("Execution policy changed after planning.")
        if preview.preflight.canonical_digest() != plan.preflight_digest:
            issues.append("Execution preflight changed after planning.")
        if preview.vendor_identity.trust.decision is not VendorTrustDecision.TRUSTED_FOR_EXECUTION:
            issues.append("Executable trust evidence is insufficient.")
        if preview.execution_assessment.decision is not VendorExecutionDecision.ALLOW:
            issues.append("Software execution policy blocked this target.")
        if preview.preflight.state is not VendorPreflightState.READY:
            issues.append("Process/service preflight is not ready.")
        if preview.execution_assessment.risk_level is not plan.risk_level:
            issues.append("Execution risk changed after planning.")
        if not preview.executable:
            issues.append("Preview is not executable.")
        return VendorUninstallSafetyReview(not issues, tuple(issues))
