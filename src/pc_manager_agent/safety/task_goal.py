"""Explicit goal confinement for graphs, delegations, and tool proposals."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, model_validator

from pc_manager_agent.domain.agents import AgentDelegationRequest, AgentToolProposal
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.task_graph import TaskDomain, TaskGraph


class TaskGoalBoundaryError(RuntimeError):
    """Raised when proposed work expands beyond the root task."""


class TaskGoalBoundary(FrozenModel):
    """Deterministically compiled scope for one root request."""

    task_id: UUID
    goal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    allowed_domains: tuple[TaskDomain, ...]
    allowed_capabilities: tuple[str, ...] = ()
    allowed_reference_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_unique_scope(self) -> TaskGoalBoundary:
        """Reject empty or duplicate boundary entries."""
        if not self.allowed_domains:
            raise ValueError("Goal boundary requires at least one domain")
        values = (
            self.allowed_domains,
            self.allowed_capabilities,
            self.allowed_reference_ids,
        )
        if any(len(items) != len(set(items)) for items in values):
            raise ValueError("Goal boundary values must be unique")
        return self


class TaskGoalBoundaryPolicy:
    """Require exact task, goal, domain, capability, and reference membership."""

    def validate_graph(self, boundary: TaskGoalBoundary, graph: TaskGraph) -> None:
        """Reject graphs that add an unapproved domain."""
        if graph.task_id != boundary.task_id or graph.goal_digest != boundary.goal_digest:
            raise TaskGoalBoundaryError("Task graph does not match the root goal")
        domains = {node.domain for node in graph.nodes if node.domain is not TaskDomain.GENERAL}
        if not domains.issubset(set(boundary.allowed_domains)):
            raise TaskGoalBoundaryError("Task graph expands the approved domain set")

    def validate_delegation(
        self, boundary: TaskGoalBoundary, request: AgentDelegationRequest
    ) -> None:
        """Reject a child objective or reference outside the parent boundary."""
        if (
            request.parent_task_id != boundary.task_id
            or request.goal_digest != boundary.goal_digest
        ):
            raise TaskGoalBoundaryError("Delegation does not match the root goal")
        if not set(request.allowed_capabilities).issubset(boundary.allowed_capabilities):
            raise TaskGoalBoundaryError("Delegation expands approved capabilities")
        references = {item.reference_id for item in request.context_refs}
        if not references.issubset(boundary.allowed_reference_ids):
            raise TaskGoalBoundaryError("Delegation expands approved references")

    def validate_proposal(self, boundary: TaskGoalBoundary, proposal: AgentToolProposal) -> None:
        """Require the proposal tool to be explicitly enabled for this root task."""
        if proposal.task_id != boundary.task_id:
            raise TaskGoalBoundaryError("Tool proposal belongs to another task")
        if proposal.tool_name not in boundary.allowed_capabilities:
            raise TaskGoalBoundaryError("Tool proposal expands the root task capability set")
