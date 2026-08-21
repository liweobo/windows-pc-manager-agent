"""Independent semantic review of Stage 4D2A plans and Previews."""

from __future__ import annotations

from dataclasses import dataclass

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiExecutionDecision,
    MsiPreflightState,
    MsiUninstallPlan,
    MsiUninstallPreview,
)
from pc_manager_agent.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class MsiUninstallSafetyReview:
    """Independent approval result with non-sensitive reasons."""

    approved: bool
    issues: tuple[str, ...]


class MsiUninstallSafetyValidator:
    """Ensure the dedicated registry and immutable evidence cannot expand scope."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def review(
        self,
        plan: MsiUninstallPlan,
        preview: MsiUninstallPreview,
    ) -> MsiUninstallSafetyReview:
        """Reject any mismatch, blocked Preview, or generalized tool manifest."""
        issues: list[str] = []
        if self._registry.names != ("software.uninstall.msi",):
            issues.append("Stage 4D2A registry must contain exactly one MSI uninstall tool.")
            return MsiUninstallSafetyReview(False, tuple(issues))
        manifest = self._registry.manifest("software.uninstall.msi")
        if (
            manifest.read_only
            or manifest.risk_level is not RiskLevel.R2
            or manifest.rollback_level is not RollbackLevel.NONE
            or not manifest.irreversible
            or not manifest.requires_runtime_confirmation
            or not manifest.supports_preview
            or manifest.max_batch_size != 1
        ):
            issues.append("MSI uninstall manifest weakens the irreversible R2 boundary.")
        if preview.plan_id != plan.plan_id or preview.plan_digest != plan.canonical_digest():
            issues.append("Preview does not match the immutable plan.")
        if preview.transaction_id != plan.transaction_id:
            issues.append("Preview transaction does not match the plan.")
        if preview.identity_digest != plan.identity_digest:
            issues.append("Preview identity does not match the plan.")
        if preview.validated_product.evidence_digest() != plan.validated_product_digest:
            issues.append("Validated MSI product changed after planning.")
        if preview.capability.canonical_digest() != plan.capability_digest:
            issues.append("MSI capability changed after planning.")
        if preview.execution_assessment.canonical_digest() != plan.execution_assessment_digest:
            issues.append("Execution policy changed after planning.")
        if preview.preflight.canonical_digest() != plan.preflight_digest:
            issues.append("Execution preflight changed after planning.")
        if preview.execution_assessment.risk_level is not plan.risk_level:
            issues.append("Execution risk changed after planning.")
        if preview.execution_assessment.decision is not MsiExecutionDecision.ALLOW:
            issues.append("Software execution policy blocked this product.")
        if preview.preflight.state is not MsiPreflightState.READY:
            issues.append("Process/service preflight is not ready.")
        if not preview.executable:
            issues.append("Preview is not executable.")
        return MsiUninstallSafetyReview(not issues, tuple(issues))
