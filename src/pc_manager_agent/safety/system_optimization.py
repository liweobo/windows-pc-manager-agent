"""Independent Stage 4E1 plan review and report-authority denial."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from pc_manager_agent.authorization.service import AuthorizedPathService
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.system_diagnostics import SystemCollector
from pc_manager_agent.domain.system_optimization import OptimizationPlan, OptimizationToolName
from pc_manager_agent.tools.registry import ToolRegistry, ToolRegistryError


class OptimizationSafetyIssue(BaseModel):
    """One deterministic reason a Stage 4E1 plan was rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str
    message: str


class OptimizationSafetyReview(BaseModel):
    """Complete independent review result."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    approved: bool
    issues: tuple[OptimizationSafetyIssue, ...] = ()


class OptimizationAuthorityError(PermissionError):
    """Raised whenever a Stage 4E1 artifact is offered as write authority."""


def reject_stage4e1_execution_authority(_artifact: object) -> None:
    """Categorically reject plans, reports, candidate IDs, and selections as write authority."""
    raise OptimizationAuthorityError(
        "Stage 4E1 artifacts are report-only and cannot authorize a cleanup operation"
    )


class SystemOptimizationSafetyValidator:
    """Require the isolated R0 registry, minimal plans, and Fresh authorized roots."""

    def __init__(self, registry: ToolRegistry, authorized_paths: AuthorizedPathService) -> None:
        self._registry = registry
        self._authorized_paths = authorized_paths

    def review(self, plan: OptimizationPlan) -> OptimizationSafetyReview:
        """Return all violations without executing collectors or resolving path text."""
        issues: list[OptimizationSafetyIssue] = []
        allow_list = tuple(item.value for item in OptimizationToolName)
        if self._registry.names != tuple(sorted(allow_list)):
            issues.append(
                OptimizationSafetyIssue(
                    code="registry-boundary",
                    message="Stage 4E1 registry must contain exactly five read-only tools",
                )
            )
        expected_tools = tuple(item for item in OptimizationToolName if item in plan.tools)
        if (
            plan.tools != expected_tools
            or len(set(plan.tools)) != len(plan.tools)
            or not plan.tools
            or plan.tools[0] is not OptimizationToolName.SNAPSHOT
            or plan.tools[-1] is not OptimizationToolName.RECOMMENDATIONS
            or (
                (OptimizationToolName.STORAGE_ANALYZE in plan.tools)
                != (OptimizationToolName.CLEANUP_CANDIDATES_ANALYZE in plan.tools)
            )
        ):
            issues.append(
                OptimizationSafetyIssue(
                    code="tool-order", message="Plan tool subset or dependencies changed"
                )
            )
        expected_collectors = tuple(
            item for item in SystemCollector if item in plan.snapshot_collectors
        )
        if (
            plan.snapshot_collectors != expected_collectors
            or len(set(plan.snapshot_collectors)) != len(plan.snapshot_collectors)
            or not plan.snapshot_collectors
            or (
                OptimizationToolName.STORAGE_ANALYZE in plan.tools
                and SystemCollector.DISKS not in plan.snapshot_collectors
            )
        ):
            issues.append(
                OptimizationSafetyIssue(
                    code="collector-scope",
                    message="Snapshot collector subset or storage dependency changed",
                )
            )
        if plan.risk_level is not RiskLevel.R0 or not plan.read_only:
            issues.append(OptimizationSafetyIssue(code="risk", message="Plan must be read-only R0"))
        if (
            plan.rollback_level is not RollbackLevel.NONE
            or plan.requires_runtime_confirmation
            or plan.estimated_system_changes != 0
            or plan.stage4e1_executable
        ):
            issues.append(
                OptimizationSafetyIssue(
                    code="execution-boundary",
                    message="Stage 4E1 cannot contain runtime confirmation or system changes",
                )
            )
        for tool_name in plan.tools:
            try:
                manifest = self._registry.manifest(tool_name.value)
            except ToolRegistryError as exc:
                issues.append(
                    OptimizationSafetyIssue(
                        code="unknown-tool", message=f"{tool_name.value}: {exc}"
                    )
                )
                continue
            if (
                manifest.risk_level is not RiskLevel.R0
                or not manifest.read_only
                or manifest.requires_runtime_confirmation
                or manifest.rollback_level is not RollbackLevel.NONE
            ):
                issues.append(
                    OptimizationSafetyIssue(
                        code="unsafe-manifest",
                        message=f"{tool_name.value} violates the R0 manifest contract",
                    )
                )
        try:
            records = self._authorized_paths.resolve_authorized(plan.authorized_root_ids)
            current_paths = tuple(item.path for item in records)
            if current_paths != plan.authorized_roots:
                raise PermissionError("Authorized root identity changed")
            for path in current_paths:
                self._authorized_paths.require_authorized(path)
        except (PermissionError, ValueError) as exc:
            issues.append(
                OptimizationSafetyIssue(
                    code="authorized-scope", message=f"Authorized path is stale: {exc}"
                )
            )
        return OptimizationSafetyReview(approved=not issues, issues=tuple(issues))
