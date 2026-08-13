"""Independent semantic and manifest validation for Stage 4A process plans."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from pc_manager_agent.domain.process_actions import (
    ProcessActionPlan,
    ProcessActionPreview,
    ProcessActionType,
    ProcessSafetyDecision,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.tools.registry import ToolRegistry, ToolRegistryError


class ProcessSafetyReview(BaseModel):
    """Machine-readable independent review; any issue denies confirmation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    approved: bool
    issues: tuple[str, ...]


class ProcessActionSafetyValidator:
    """Verify exact tools, risks, schemas, target bounds, policy, and rollback truth."""

    def __init__(
        self, registry: ToolRegistry, max_applications: int = 5, max_processes: int = 20
    ) -> None:
        self._registry = registry
        self._max_applications = max_applications
        self._max_processes = max_processes

    def review(
        self,
        plan: ProcessActionPlan,
        preview: ProcessActionPreview,
    ) -> ProcessSafetyReview:
        """Return approval only when every execution-relevant binding is exact."""
        issues: list[str] = []
        tool_name = _tool_name(plan.action)
        try:
            manifest = self._registry.manifest(tool_name)
        except ToolRegistryError as exc:
            issues.append(f"Tool is not registered: {exc}")
            return ProcessSafetyReview(approved=False, issues=tuple(issues))
        expected_risk = (
            RiskLevel.R2
            if plan.action is ProcessActionType.REQUEST_GRACEFUL_EXIT
            else RiskLevel.R2_HIGH_IMPACT
        )
        if manifest.risk_level is not expected_risk or plan.risk_level is not expected_risk:
            issues.append("Process action risk does not match the registered manifest")
        if (
            manifest.read_only
            or not manifest.supports_preview
            or not manifest.requires_runtime_confirmation
            or manifest.rollback_level is not RollbackLevel.NONE
            or not manifest.irreversible
        ):
            issues.append("Process tool manifest weakens the irreversible R2 safety contract")
        if (
            preview.plan_id != plan.plan_id
            or preview.transaction_id != plan.transaction_id
            or preview.plan_digest != plan.canonical_digest()
            or preview.action is not plan.action
            or preview.target_set_digest != plan.target_set_digest()
        ):
            issues.append("Process Preview is stale or does not match the plan")
        if len(plan.targets) > self._max_applications:
            issues.append("Process application batch exceeds the configured limit")
        if plan.estimated_processes_affected > self._max_processes:
            issues.append("Process member batch exceeds the configured limit")
        if len(preview.assessments) != preview.process_count:
            issues.append("Every process member requires an explicit policy assessment")
        if any(
            assessment.decision is not ProcessSafetyDecision.ALLOW
            for assessment in preview.assessments
        ):
            issues.append("At least one concrete process target is blocked by safety policy")
        arguments = {
            "action": plan.action.value,
            "identities": [
                member.identity.model_dump(mode="json")
                for target in plan.targets
                for member in target.members
            ],
            "target_set_digest": plan.target_set_digest(),
            "timeout_seconds": 10.0,
        }
        try:
            self._registry.validate_input(tool_name, arguments)
        except ToolRegistryError as exc:
            issues.append(f"Registered process tool rejected the exact targets: {exc}")
        return ProcessSafetyReview(approved=not issues, issues=tuple(issues))


def _tool_name(action: ProcessActionType) -> str:
    return (
        "system.process.request_exit"
        if action is ProcessActionType.REQUEST_GRACEFUL_EXIT
        else "system.process.force_terminate"
    )
