"""Metadata-only audit facade for the Stage 5E Final Orchestrator."""

from __future__ import annotations

from uuid import UUID

from pc_manager_agent import __version__
from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.domain.computer_tasks import ComputerTaskState
from pc_manager_agent.domain.risk import RiskLevel


class ComputerTaskAuditLogger:
    """Write IDs, digests, counters, and reason codes without task or domain bodies."""

    def __init__(self, repository: AuditRepository, git_commit: str | None = None) -> None:
        self._repository = repository
        self._git_commit = git_commit

    def lifecycle(
        self,
        task_id: UUID,
        *,
        event_code: str,
        state: ComputerTaskState,
        graph_version: int,
        graph_digest: str,
        node_id: UUID | None = None,
        detail_codes: tuple[str, ...] = (),
    ) -> None:
        """Record one content-free task lifecycle transition."""
        self._repository.record(
            AuditEvent(
                event_type="computer_task.lifecycle",
                plan_id=str(task_id),
                step_id=None if node_id is None else str(node_id),
                risk_level=RiskLevel.R0,
                agent_decision=event_code,
                parameters={
                    "task_id": str(task_id),
                    "state": state.value,
                    "graph_version": graph_version,
                    "graph_digest": graph_digest,
                    "detail_codes": list(detail_codes),
                    "goal_body_saved": False,
                    "domain_body_saved": False,
                    "confirmation_sensitive_material_saved": False,
                },
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )

    def dispatch(
        self,
        task_id: UUID,
        node_id: UUID,
        *,
        event_code: str,
        dispatch_id: UUID,
        domain_code: str,
        attempt_count: int,
    ) -> None:
        """Record high-level handoff identity without target data or executable arguments."""
        self._repository.record(
            AuditEvent(
                event_type="computer_task.dispatch",
                plan_id=str(task_id),
                step_id=str(node_id),
                risk_level=RiskLevel.R0,
                agent_decision=event_code,
                parameters={
                    "dispatch_id": str(dispatch_id),
                    "domain": domain_code,
                    "attempt_count": attempt_count,
                    "low_level_tool_saved": False,
                },
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )
