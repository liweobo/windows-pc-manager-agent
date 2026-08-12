"""Independent deterministic safety review for Stage 3 diagnostic plans."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.system_diagnostics import DiagnosticPlan, SystemCollector
from pc_manager_agent.tools.registry import ToolRegistry, ToolRegistryError


class DiagnosticSafetyIssue(BaseModel):
    """One actionable reason a diagnostic plan was rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str
    message: str


class DiagnosticSafetyReview(BaseModel):
    """Independent review result consumed by orchestration and UI."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    approved: bool
    issues: tuple[DiagnosticSafetyIssue, ...] = ()


class DiagnosticSafetyValidator:
    """Verify registry membership, schemas, risk, confirmation, and immutable limits."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def review(self, plan: DiagnosticPlan) -> DiagnosticSafetyReview:
        """Return all detected policy violations without executing any collector."""
        issues: list[DiagnosticSafetyIssue] = []
        if plan.risk_level is not RiskLevel.R0 or not plan.read_only:
            issues.append(DiagnosticSafetyIssue(code="risk", message="Plan must be read-only R0"))
        if plan.rollback_level is not RollbackLevel.NONE:
            issues.append(
                DiagnosticSafetyIssue(code="rollback", message="R0 plans require no rollback")
            )
        if not plan.requires_plan_confirmation:
            issues.append(
                DiagnosticSafetyIssue(code="confirmation", message="Plan confirmation is required")
            )
        if plan.estimated_system_changes != 0:
            issues.append(
                DiagnosticSafetyIssue(code="impact", message="System-change count must be zero")
            )
        if len(set(plan.collectors)) != len(plan.collectors):
            issues.append(
                DiagnosticSafetyIssue(
                    code="duplicate-collector",
                    message="Diagnostic collectors must be unique",
                )
            )
        for collector in plan.collectors:
            arguments = self.arguments_for(plan, collector)
            try:
                manifest = self._registry.manifest(collector.value)
                self._registry.validate_input(collector.value, arguments)
            except ToolRegistryError as exc:
                issues.append(
                    DiagnosticSafetyIssue(code="registry", message=f"{collector.value}: {exc}")
                )
                continue
            if manifest.risk_level is not RiskLevel.R0 or not manifest.read_only:
                issues.append(
                    DiagnosticSafetyIssue(
                        code="manifest-risk",
                        message=f"{collector.value} is not declared read-only R0",
                    )
                )
            if not manifest.requires_confirmation or manifest.requires_runtime_confirmation:
                issues.append(
                    DiagnosticSafetyIssue(
                        code="manifest-confirmation",
                        message=f"{collector.value} has an invalid confirmation policy",
                    )
                )
            if manifest.rollback_level is not RollbackLevel.NONE:
                issues.append(
                    DiagnosticSafetyIssue(
                        code="manifest-rollback",
                        message=f"{collector.value} has an invalid R0 rollback declaration",
                    )
                )
        return DiagnosticSafetyReview(approved=not issues, issues=tuple(issues))

    @staticmethod
    def arguments_for(plan: DiagnosticPlan, collector: SystemCollector) -> dict[str, object]:
        """Derive every executable argument locally from bounded plan fields."""
        if collector is SystemCollector.CPU:
            return {
                "sample_count": plan.sample_count,
                "interval_seconds": plan.sample_interval_seconds,
            }
        if collector is SystemCollector.PROCESSES:
            return {
                "sample_interval_seconds": plan.sample_interval_seconds,
                "max_processes": plan.max_processes,
            }
        if collector in {
            SystemCollector.STARTUP,
            SystemCollector.SERVICES,
            SystemCollector.SOFTWARE,
        }:
            return {"max_items": plan.max_items_per_collector}
        return {}
