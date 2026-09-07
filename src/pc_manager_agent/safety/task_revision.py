"""User-only graph revision and scope-expansion validation."""

from __future__ import annotations

import hashlib

from pc_manager_agent.domain.computer_tasks import ComputerTask, TaskRevisionRequest
from pc_manager_agent.domain.task_workflows import DomainType
from pc_manager_agent.safety.final_orchestrator import FinalOrchestratorSafetyError


class TaskRevisionValidator:
    """Ensure web, document, model, and Memory content cannot silently revise a task."""

    def validate(
        self,
        task: ComputerTask,
        request: TaskRevisionRequest,
        *,
        current_domains: tuple[DomainType, ...],
    ) -> tuple[DomainType, ...]:
        """Return the exact new domain set after version and expansion checks."""
        if request.task_id != task.task_id or request.expected_graph_version != task.graph_version:
            raise FinalOrchestratorSafetyError("Task revision is stale or belongs elsewhere")
        if not request.user_initiated:
            raise FinalOrchestratorSafetyError("Only an explicit user action may revise a task")
        try:
            requested = tuple(DomainType(code) for code in request.requested_domain_codes)
        except ValueError as exc:
            raise FinalOrchestratorSafetyError("Task revision contains an unknown domain") from exc
        if not requested or len(requested) != len(set(requested)):
            raise FinalOrchestratorSafetyError("Task revision domains must be non-empty and unique")
        expansion = set(requested) - set(current_domains)
        if expansion and not request.scope_expansion_approved:
            raise FinalOrchestratorSafetyError("Task revision requires scope-expansion approval")
        if hashlib.sha256(request.new_goal.encode()).hexdigest() == task.goal_digest:
            raise FinalOrchestratorSafetyError("Task revision did not change the goal")
        return requested
