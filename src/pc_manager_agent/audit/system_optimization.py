"""Aggregate-only audit events for Stage 4E1."""

from __future__ import annotations

from pc_manager_agent import __version__
from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.system_optimization import OptimizationPlan, SystemOptimizationReport


class SystemOptimizationAuditLogger:
    """Persist workflow evidence without candidate paths, names, or document content."""

    def __init__(self, repository: AuditRepository, git_commit: str | None = None) -> None:
        self._repository = repository
        self._git_commit = git_commit

    def plan_reviewed(
        self, plan: OptimizationPlan, approved: bool, issues: tuple[str, ...]
    ) -> None:
        """Record goals, limits and tool allow-list with path counts only."""
        self._repository.record(
            AuditEvent(
                event_type="optimization.plan.reviewed",
                # Stage 4E1 stores goal categories below instead of free-form text because
                # a user request can contain local file names or paths.
                original_request="Stage 4E1 local optimization analysis request (details redacted)",
                plan={
                    "plan_id": str(plan.plan_id),
                    "version": plan.version,
                    "goals": [item.value for item in plan.goals],
                    "tools": [item.value for item in plan.tools],
                    "snapshot_collectors": [item.value for item in plan.snapshot_collectors],
                    "authorized_root_count": len(plan.authorized_roots),
                    "max_objects": plan.max_objects,
                    "timeout_seconds": plan.timeout_seconds,
                    "read_only": True,
                    "estimated_system_changes": 0,
                },
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

    def confirmation_resolved(self, plan: OptimizationPlan, approved: bool) -> None:
        """Record the decision without treating it as future cleanup authority."""
        self._repository.record(
            AuditEvent(
                event_type="optimization.confirmation.resolved",
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result="APPROVED" if approved else "REJECTED",
                result={"analysis_authorized": approved, "cleanup_authorized": False},
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )

    def tool_completed(self, plan: OptimizationPlan, tool_name: str, item_count: int) -> None:
        """Record only the tool name and aggregate count."""
        self._repository.record(
            AuditEvent(
                event_type="optimization.tool.completed",
                plan_id=str(plan.plan_id),
                plan_version=plan.version,
                tool_name=tool_name,
                parameters={"bounded": True},
                result={"item_count": item_count},
                verification={"read_only": True, "system_changes": 0},
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result="APPROVED",
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )

    def report_completed(self, report: SystemOptimizationReport) -> None:
        """Record aggregate storage and finding counts, never candidate details."""
        self._repository.record(
            AuditEvent(
                event_type="optimization.report.completed",
                plan_id=str(report.plan_id),
                risk_level=RiskLevel.R0,
                confirmation_required=True,
                confirmation_result="APPROVED",
                result={
                    "report_id": str(report.report_id),
                    "candidate_count": len(report.cleanup_candidates),
                    "finding_count": len(report.performance_findings),
                    "recommendation_count": len(report.recommendations),
                    "observed_bytes": report.observed_bytes,
                    "potential_reclaim_bytes": report.potential_reclaim_bytes,
                    "protected_bytes": report.protected_bytes,
                    "unknown_bytes": report.unknown_bytes,
                    "partial_source_count": len(report.partial_sources),
                    "skipped_source_count": len(report.skipped_sources),
                    "changes_performed": False,
                },
                verification={"read_only": True, "system_changes": 0},
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )
