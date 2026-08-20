"""Independent Stage 4D1 plan and Preview safety validation."""

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.software_uninstall_analysis import (
    SoftwareUninstallAnalysisPlan,
    SoftwareUninstallPreview,
)
from pc_manager_agent.safety.software_zero_execution import (
    STAGE_4D1_TOOL_NAMES,
    SoftwareZeroExecutionGuard,
)
from pc_manager_agent.tools.registry import ToolRegistry, ToolRegistryError


class SoftwareSafetyIssue(FrozenModel):
    """One independent validation problem."""

    code: str
    message: str


class SoftwareSafetyReview(FrozenModel):
    """Aggregate Stage 4D1 validation result."""

    approved: bool
    issues: tuple[SoftwareSafetyIssue, ...] = ()


class SoftwareUninstallSafetyValidator:
    """Verify the R0 allow-list, schemas, digests, and immutable zero-execution fields."""

    def __init__(self, registry: ToolRegistry, guard: SoftwareZeroExecutionGuard) -> None:
        self._registry = registry
        self._guard = guard

    def review_plan(self, plan: SoftwareUninstallAnalysisPlan) -> SoftwareSafetyReview:
        """Review a plan without querying software or executing tools."""
        issues: list[SoftwareSafetyIssue] = []
        try:
            self._guard.validate_registry(self._registry)
        except RuntimeError as exc:
            issues.append(SoftwareSafetyIssue(code="registry", message=str(exc)))
        if tuple(plan.tool_names) != STAGE_4D1_TOOL_NAMES:
            issues.append(
                SoftwareSafetyIssue(
                    code="tool-set",
                    message="Plan does not contain the exact ordered Stage 4D1 tool set",
                )
            )
        if plan.risk_level is not RiskLevel.R0 or not plan.read_only:
            issues.append(SoftwareSafetyIssue(code="risk", message="Plan must be read-only R0"))
        if plan.rollback_level is not RollbackLevel.NONE or plan.estimated_system_changes != 0:
            issues.append(
                SoftwareSafetyIssue(code="impact", message="Plan cannot modify or roll back state")
            )
        arguments: dict[str, dict[str, object]] = {
            "software.inventory": {"max_items": plan.max_items},
            "software.resolve": {
                "query": plan.target_query.model_dump(mode="json"),
                "max_items": plan.max_items,
            },
            "software.inspect": {
                "identity_digest": "0" * 64,
                "max_items": plan.max_items,
            },
            "software.uninstall_capability": {
                "identity_digest": "0" * 64,
                "max_items": plan.max_items,
            },
            "software.uninstall_preview": {
                "plan": plan.model_dump(mode="json"),
                "identity_digest": "0" * 64,
            },
        }
        for name, values in arguments.items():
            try:
                self._registry.validate_input(name, values)
            except ToolRegistryError as exc:
                issues.append(SoftwareSafetyIssue(code="schema", message=f"{name}: {exc}"))
        return SoftwareSafetyReview(approved=not issues, issues=tuple(issues))

    def review_preview(
        self,
        plan: SoftwareUninstallAnalysisPlan,
        preview: SoftwareUninstallPreview,
    ) -> SoftwareSafetyReview:
        """Verify exact plan/identity/capability bindings and mandatory stop state."""
        issues: list[SoftwareSafetyIssue] = []
        if preview.plan_id != plan.plan_id or preview.plan_digest != plan.canonical_digest():
            issues.append(SoftwareSafetyIssue(code="plan-digest", message="Preview plan changed"))
        if preview.executable_in_current_stage or preview.execution_performed:
            issues.append(
                SoftwareSafetyIssue(
                    code="execution", message="Preview implies prohibited execution"
                )
            )
        if preview.analysis_risk_level is not RiskLevel.R0:
            issues.append(SoftwareSafetyIssue(code="risk", message="Preview analysis is not R0"))
        if preview.identity_digest != preview.target.identity.canonical_digest():
            issues.append(
                SoftwareSafetyIssue(code="identity", message="Preview target identity changed")
            )
        if preview.capability_digest != preview.capability.canonical_digest():
            issues.append(
                SoftwareSafetyIssue(code="capability", message="Preview capability changed")
            )
        if not preview.stop_reason.strip():
            issues.append(SoftwareSafetyIssue(code="stop", message="Preview lacks a stop reason"))
        return SoftwareSafetyReview(approved=not issues, issues=tuple(issues))
