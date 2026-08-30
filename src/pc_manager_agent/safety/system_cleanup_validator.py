"""Independent semantic review for executable Stage 4E2 item cleanup plans."""

from __future__ import annotations

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.system_cleanup_execution import (
    CleanupAction,
    CleanupAdapterType,
    CleanupEligibilityDecision,
    CleanupExecutionPlan,
)
from pc_manager_agent.safety.plan_reviewer import ReviewIssue, SafetyReview
from pc_manager_agent.tools.registry import ToolRegistry, UnknownToolError


class SystemCleanupSafetyValidator:
    """Reject missing evidence, unsafe manifests, and every non-Recycle-Bin action."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def review(self, plan: CleanupExecutionPlan) -> SafetyReview:
        """Review every item and the batch contract without trusting the Planner or UI."""
        issues: list[ReviewIssue] = []
        if plan.risk_level not in {RiskLevel.R2, RiskLevel.R2_HIGH_IMPACT}:
            issues.append(ReviewIssue(code="invalid-risk", message="Cleanup must be R2"))
        if plan.rollback_level is not RollbackLevel.MANUAL:
            issues.append(
                ReviewIssue(code="invalid-recovery", message="Cleanup recovery must be MANUAL")
            )
        for item in plan.items:
            step_id = str(item.operation_id)
            candidate = item.candidate
            if item.action is not CleanupAction.MOVE_TO_RECYCLE_BIN:
                issues.append(
                    ReviewIssue(
                        code="invalid-action",
                        message="Only MOVE_TO_RECYCLE_BIN is allowed",
                        step_id=step_id,
                    )
                )
            if (
                candidate.eligibility is not CleanupEligibilityDecision.ELIGIBLE
                or candidate.cleanup_adapter_type is not CleanupAdapterType.RECYCLE_BIN_ITEM
            ):
                issues.append(
                    ReviewIssue(
                        code="candidate-not-eligible",
                        message="A blocked or routed candidate entered the direct plan",
                        step_id=step_id,
                    )
                )
            if any(
                value is None
                for value in (
                    candidate.path,
                    candidate.fresh_identity,
                    candidate.material,
                    candidate.path_safety,
                    candidate.activity,
                    candidate.recoverability_evidence,
                )
            ):
                issues.append(
                    ReviewIssue(
                        code="fresh-evidence-incomplete",
                        message="Executable candidate lacks complete Fresh evidence",
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
                manifest.name != "optimization.cleanup.trash"
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
                        message="Cleanup trash tool does not satisfy its maximum R2 contract",
                        step_id=step_id,
                    )
                )
        return SafetyReview(approved=not issues, issues=tuple(issues))
