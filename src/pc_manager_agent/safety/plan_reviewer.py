"""Independent deterministic safety review of structured plans."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from pc_manager_agent.domain.plans import TaskPlan
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.safety.path_policy import PathPolicy, PathSecurityError
from pc_manager_agent.tools.registry import ToolInputError, ToolRegistry, UnknownToolError


class ReviewIssue(BaseModel):
    """One machine-readable safety-review failure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    step_id: str | None = None


class SafetyReview(BaseModel):
    """Final review result; any issue denies execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    approved: bool
    issues: tuple[ReviewIssue, ...]


class SafetyReviewer:
    """Check tool, schema, risk, confirmation, rollback, and path scope."""

    def __init__(self, registry: ToolRegistry, path_policy: PathPolicy) -> None:
        self._registry = registry
        self._path_policy = path_policy

    def review(self, plan: TaskPlan) -> SafetyReview:
        """Review a plan without executing any operation."""
        issues: list[ReviewIssue] = []
        if not plan.requires_plan_confirmation:
            issues.append(
                ReviewIssue(
                    code="plan-confirmation-required", message="Complex plans require confirmation"
                )
            )
        for included_path in plan.scope.included_paths:
            try:
                self._path_policy.validate_scan_root(included_path)
            except PathSecurityError as exc:
                issues.append(ReviewIssue(code="invalid-scope", message=str(exc)))

        for step in plan.steps:
            try:
                manifest = self._registry.manifest(step.tool_name)
            except UnknownToolError:
                issues.append(
                    ReviewIssue(
                        code="unknown-tool",
                        message=f"Tool is not registered: {step.tool_name}",
                        step_id=step.step_id,
                    )
                )
                continue
            try:
                self._registry.validate_input(step.tool_name, step.arguments)
            except ToolInputError as exc:
                issues.append(
                    ReviewIssue(code="invalid-arguments", message=str(exc), step_id=step.step_id)
                )
            if step.risk_level is not manifest.risk_level:
                issues.append(
                    ReviewIssue(
                        code="risk-mismatch",
                        message="Plan risk does not match the registered manifest",
                        step_id=step.step_id,
                    )
                )
            if step.rollback_level is not manifest.rollback_level:
                issues.append(
                    ReviewIssue(
                        code="rollback-mismatch",
                        message="Plan rollback claim does not match the registered manifest",
                        step_id=step.step_id,
                    )
                )
            if (
                manifest.risk_level.severity >= RiskLevel.R2.severity
                and not step.requires_confirmation
            ):
                issues.append(
                    ReviewIssue(
                        code="runtime-confirmation-required",
                        message="R2 or higher requires immediate confirmation",
                        step_id=step.step_id,
                    )
                )
            if manifest.risk_level in {RiskLevel.R3, RiskLevel.R4}:
                issues.append(
                    ReviewIssue(
                        code="mvp-risk-denied",
                        message=f"{manifest.risk_level} execution is unavailable in MVP 0.1",
                        step_id=step.step_id,
                    )
                )
            for argument_name in manifest.scope_argument_names:
                raw_path = step.arguments.get(argument_name)
                if not isinstance(raw_path, str) or not self._path_policy.is_approved(
                    Path(raw_path)
                ):
                    issues.append(
                        ReviewIssue(
                            code="scope-expansion",
                            message=f"Argument {argument_name!r} is outside approved scope",
                            step_id=step.step_id,
                        )
                    )
        return SafetyReview(approved=not issues, issues=tuple(issues))
