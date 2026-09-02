"""Aggregate-only Stage 5D Agent and task provenance audit."""

from __future__ import annotations

from uuid import UUID

from pc_manager_agent import __version__
from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.domain.agents import AgentRole
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.task_outcomes import RootTaskStatus


class MultiAgentAuditLogger:
    """Record IDs, digests, counts, and versions without prompts or payload bodies."""

    def __init__(self, repository: AuditRepository, git_commit: str | None = None) -> None:
        self._repository = repository
        self._git_commit = git_commit

    def task_event(
        self,
        task_id: UUID,
        *,
        event_code: str,
        status: RootTaskStatus,
        graph_digest: str,
        goal_digest: str,
        node_count: int,
        roles: tuple[AgentRole, ...],
        manifest_digests: tuple[str, ...],
        prompt_versions: tuple[str, ...],
    ) -> None:
        """Append one root-task transition without serializing the user goal."""
        self._repository.record(
            AuditEvent(
                event_type="agent.task",
                plan_id=str(task_id),
                risk_level=RiskLevel.R0,
                agent_decision=event_code,
                parameters={
                    "task_id": str(task_id),
                    "status": status.value,
                    "graph_digest": graph_digest,
                    "goal_digest": goal_digest,
                    "node_count": node_count,
                    "roles": [role.value for role in roles],
                    "manifest_digests": list(manifest_digests),
                    "prompt_versions": list(prompt_versions),
                    "prompt_body_saved": False,
                    "context_body_saved": False,
                },
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )

    def capability_denied(
        self,
        task_id: UUID,
        node_id: UUID,
        role: AgentRole,
        reason_code: str,
    ) -> None:
        """Record a blocked boundary using fixed metadata only."""
        self._repository.record(
            AuditEvent(
                event_type="agent.capability_denied",
                plan_id=str(task_id),
                step_id=str(node_id),
                risk_level=RiskLevel.R0,
                parameters={
                    "task_id": str(task_id),
                    "node_id": str(node_id),
                    "role": role.value,
                    "reason_code": reason_code,
                },
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )
