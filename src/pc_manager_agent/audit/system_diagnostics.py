"""Privacy-minimized audit events for Stage 3 diagnostic workflows."""

from __future__ import annotations

from pc_manager_agent import __version__
from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.system_diagnostics import (
    CollectorOutcome,
    DiagnosticPlan,
    DiagnosticReport,
)


class DiagnosticAuditLogger:
    """Write required audit evidence without raw processes, commands, or registry values."""

    def __init__(self, repository: AuditRepository, git_commit: str | None = None) -> None:
        self._repository = repository
        self._git_commit = git_commit

    def plan_reviewed(self, plan: DiagnosticPlan, approved: bool, issues: tuple[str, ...]) -> None:
        """Record the finite collector plan and independent safety decision."""
        self._repository.record(
            AuditEvent(
                event_type="diagnostic.plan.reviewed",
                original_request=plan.user_goal,
                plan=plan.model_dump(mode="json"),
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                agent_decision="approved" if approved else "rejected",
                result={"issues": list(issues)},
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )

    def confirmation_resolved(self, plan: DiagnosticPlan, approved: bool) -> None:
        """Record approval or rejection without retaining UI state as authority."""
        self._repository.record(
            AuditEvent(
                event_type="diagnostic.confirmation.resolved",
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result="APPROVED" if approved else "REJECTED",
                result={"executed": False},
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )

    def collector_started(self, plan: DiagnosticPlan, tool_name: str) -> None:
        """Write-ahead audit one collector before its read-only query starts."""
        self._repository.record(
            AuditEvent(
                event_type="diagnostic.collector.started",
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                tool_name=tool_name,
                parameters={"bounded": True},
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result="APPROVED",
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )

    def collector_completed(self, plan: DiagnosticPlan, outcome: CollectorOutcome) -> None:
        """Record sanitized status, counts, cache use, warnings, and timing."""
        error = outcome.error.model_dump(mode="json") if outcome.error else None
        self._repository.record(
            AuditEvent(
                event_type="diagnostic.collector.completed",
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                tool_name=outcome.collector.value,
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result="APPROVED",
                result={
                    "state": outcome.state.value,
                    "item_count": outcome.item_count,
                    "from_cache": outcome.from_cache,
                    "warning_count": len(outcome.warnings),
                },
                error=error,
                verification={"no_system_changes": True},
                duration_ms=outcome.duration_ms,
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )

    def report_completed(self, plan: DiagnosticPlan, report: DiagnosticReport) -> None:
        """Record report counts and partial status, never the raw snapshot."""
        failed = sum(outcome.state.value == "failed" for outcome in report.snapshot.outcomes)
        self._repository.record(
            AuditEvent(
                event_type="diagnostic.report.completed",
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result="APPROVED",
                result={
                    "report_id": str(report.report_id),
                    "finding_count": len(report.findings),
                    "collector_count": len(report.snapshot.outcomes),
                    "failed_collector_count": failed,
                },
                verification={"system_changes": 0, "read_only": True},
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )
