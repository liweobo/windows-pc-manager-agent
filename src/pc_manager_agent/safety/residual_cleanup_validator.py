"""Independent semantic review for executable Stage 4D4 cleanup plans."""

from __future__ import annotations

from pc_manager_agent.domain.residual_cleanup import (
    CleanupEligibilityDecision,
    ResidualCleanupAction,
    ResidualCleanupPlan,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.safety.plan_reviewer import ReviewIssue, SafetyReview
from pc_manager_agent.tools.registry import ToolRegistry, UnknownToolError


class ResidualCleanupSafetyValidator:
    """Reject stale evidence, unsafe manifests, and any non-Recycle-Bin action."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def review(self, plan: ResidualCleanupPlan) -> SafetyReview:
        """Review every exact item independently and the batch risk contract as a whole."""
        issues: list[ReviewIssue] = []
        if plan.risk_level not in {RiskLevel.R2, RiskLevel.R2_HIGH_IMPACT}:
            issues.append(
                ReviewIssue(
                    code="invalid-risk",
                    message="Residual cleanup must be R2 or R2_HIGH_IMPACT",
                )
            )
        if plan.rollback_level is not RollbackLevel.MANUAL:
            issues.append(
                ReviewIssue(
                    code="invalid-recovery",
                    message="Residual cleanup recovery must be MANUAL",
                )
            )
        for item in plan.items:
            step_id = str(item.operation_id)
            if item.action.value != ResidualCleanupAction.MOVE_TO_RECYCLE_BIN.value:
                issues.append(
                    ReviewIssue(
                        code="invalid-action",
                        message="Only MOVE_TO_RECYCLE_BIN is allowed",
                        step_id=step_id,
                    )
                )
            if item.candidate.eligibility is not CleanupEligibilityDecision.ELIGIBLE:
                issues.append(
                    ReviewIssue(
                        code="candidate-not-eligible",
                        message="A blocked candidate entered the executable plan",
                        step_id=step_id,
                    )
                )
            if (
                item.candidate.fresh_identity is None
                or item.candidate.material is None
                or item.candidate.path_safety is None
                or item.candidate.recent_activity is None
                or item.candidate.recoverability is None
            ):
                issues.append(
                    ReviewIssue(
                        code="fresh-evidence-incomplete",
                        message="Executable candidate lacks complete fresh evidence",
                        step_id=step_id,
                    )
                )
            try:
                manifest = self._registry.manifest(item.tool_name)
            except UnknownToolError:
                issues.append(
                    ReviewIssue(
                        code="unknown-tool",
                        message=f"Tool is not registered: {item.tool_name}",
                        step_id=step_id,
                    )
                )
                continue
            if (
                manifest.name != "software.residuals.trash"
                or manifest.read_only
                or manifest.risk_level is not RiskLevel.R2_HIGH_IMPACT
                or not manifest.supports_risk(plan.risk_level)
                or not manifest.requires_confirmation
                or not manifest.requires_runtime_confirmation
                or not manifest.supports_preview
                or manifest.rollback_level is not RollbackLevel.MANUAL
            ):
                issues.append(
                    ReviewIssue(
                        code="unsafe-tool-manifest",
                        message="Residual trash tool does not satisfy its maximum R2 contract",
                        step_id=step_id,
                    )
                )
        return SafetyReview(approved=not issues, issues=tuple(issues))
