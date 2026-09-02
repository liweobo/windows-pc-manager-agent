"""Explicit domain preparation Agents with no registry or executor access."""

from __future__ import annotations

from pc_manager_agent.domain.agents import (
    AgentFinding,
    AgentResult,
    AgentResultStatus,
    AgentRole,
    AgentRuntimeIdentity,
    DomainPreparationProposal,
)
from pc_manager_agent.domain.context import ContextPackage
from pc_manager_agent.domain.task_graph import TaskDomain, TaskNode


class DomainPreparationAgent:
    """Produce one reference-only handoff into an existing domain workflow."""

    def __init__(self, identity: AgentRuntimeIdentity, domain: TaskDomain) -> None:
        expected = {
            TaskDomain.FILE: AgentRole.FILE,
            TaskDomain.SYSTEM: AgentRole.SYSTEM,
            TaskDomain.SOFTWARE: AgentRole.SOFTWARE,
            TaskDomain.OFFICE: AgentRole.OFFICE,
            TaskDomain.BROWSER: AgentRole.BROWSER,
            TaskDomain.OPTIMIZATION: AgentRole.OPTIMIZATION,
            TaskDomain.MEMORY: AgentRole.MEMORY_MANAGER,
        }.get(domain)
        if expected is None or identity.role is not expected:
            raise ValueError("Agent role does not own the requested domain")
        self._identity = identity
        self._domain = domain

    @property
    def identity(self) -> AgentRuntimeIdentity:
        """Return the runtime-created identity."""
        return self._identity

    def prepare(self, node: TaskNode, context: ContextPackage) -> AgentResult:
        """Describe a safe handoff; the destination still owns every safety gate."""
        if context.agent_role != self._identity.role.value:
            raise ValueError("Context was prepared for another Agent role")
        if node.agent_role is not self._identity.role or node.domain is not self._domain:
            raise ValueError("Task node does not belong to this Agent")
        proposal = DomainPreparationProposal(
            task_id=context.task_id,
            node_id=node.node_id,
            domain=self._domain.value,
            action_code="ENTER_EXISTING_DOMAIN_PREPARATION",
            goal_digest=context.goal_digest,
            context_refs=tuple(item.reference.reference_id for item in context.items),
        )
        return AgentResult(
            task_id=context.task_id,
            node_id=node.node_id,
            status=AgentResultStatus.COMPLETED,
            findings=(
                AgentFinding(
                    code="DOMAIN_CONFIRMATION_REMAINS_REQUIRED",
                    summary="The existing domain must resolve, preview, confirm, and verify.",
                ),
            ),
            preparation_proposals=(proposal,),
        )
