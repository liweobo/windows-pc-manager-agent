"""Independent semantic review for concrete Stage 2B trash plans."""

from __future__ import annotations

from pc_manager_agent.domain.file_operations import OperationType
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.trash import TrashPlan
from pc_manager_agent.safety.path_policy import PathSecurityError, path_is_within
from pc_manager_agent.safety.plan_reviewer import ReviewIssue, SafetyReview
from pc_manager_agent.safety.trash_policy import TrashPathPolicy
from pc_manager_agent.tools.registry import ToolRegistry, UnknownToolError


class TrashSafetyValidator:
    """Reject scope expansion, overlapping selections, or a weakened R2 tool contract."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: TrashPathPolicy,
        *,
        max_selected: int = 100,
    ) -> None:
        if max_selected <= 0:
            raise ValueError("Trash selected-object limit must be positive")
        self._registry = registry
        self._policy = policy
        self._max_selected = max_selected

    def review(self, plan: TrashPlan) -> SafetyReview:
        """Review an exact plan without reading content or invoking Windows Shell."""
        issues: list[ReviewIssue] = []
        if len(plan.items) > self._max_selected:
            issues.append(
                ReviewIssue(
                    code="batch-limit-exceeded",
                    message=f"Trash plan exceeds {self._max_selected} selected objects",
                )
            )
        if (
            plan.risk_level is not RiskLevel.R2
            or not plan.requires_plan_confirmation
            or not plan.requires_runtime_confirmation
            or plan.rollback_level is not RollbackLevel.MANUAL
        ):
            issues.append(
                ReviewIssue(
                    code="invalid-risk-contract",
                    message="Trash plans require R2, two confirmations, and MANUAL recovery",
                )
            )
        sources = []
        for item in plan.items:
            step_id = str(item.operation_id)
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
                manifest.name != "file.trash"
                or manifest.risk_level is not RiskLevel.R2
                or manifest.read_only
                or not manifest.requires_confirmation
                or not manifest.requires_runtime_confirmation
                or not manifest.supports_preview
                or manifest.rollback_level is not RollbackLevel.MANUAL
            ):
                issues.append(
                    ReviewIssue(
                        code="unsafe-tool-manifest",
                        message="Registered trash tool does not satisfy the R2 contract",
                        step_id=step_id,
                    )
                )
            if item.operation_type not in {
                OperationType.RECYCLE_FILE,
                OperationType.RECYCLE_DIRECTORY,
            }:
                issues.append(
                    ReviewIssue(
                        code="invalid-operation-type",
                        message="Trash plans may contain only recycle operations",
                        step_id=step_id,
                    )
                )
            try:
                source = self._policy.validate_source(item.source)
            except (OSError, PathSecurityError) as exc:
                issues.append(ReviewIssue(code="unsafe-source", message=str(exc), step_id=step_id))
                continue
            sources.append((source, item.expected_source_state.kind))
        for index, (source, kind) in enumerate(sources):
            for other, _other_kind in sources[index + 1 :]:
                if source == other:
                    issues.append(
                        ReviewIssue(
                            code="duplicate-source",
                            message=f"Source is selected more than once: {source}",
                        )
                    )
                overlapping = (kind.value == "DIRECTORY" and path_is_within(other, source)) or (
                    _other_kind.value == "DIRECTORY" and path_is_within(source, other)
                )
                if overlapping:
                    issues.append(
                        ReviewIssue(
                            code="overlapping-sources",
                            message=f"A child is selected separately from its parent: {other}",
                        )
                    )
        return SafetyReview(approved=not issues, issues=tuple(issues))
