"""Provider-neutral structured task-graph proposal contract."""

from __future__ import annotations

import hashlib
from typing import Protocol
from uuid import UUID

from pydantic import Field, model_validator

from pc_manager_agent.domain.agents import AgentRole
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.task_graph import TaskDomain, TaskNodeType


class AgentGraphDraftNode(FrozenModel):
    """Provider-proposed node with no status, authorization, or resource identity."""

    node_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    node_type: TaskNodeType
    domain: TaskDomain
    agent_role: AgentRole
    risk_hint: RiskLevel = RiskLevel.R0


class AgentGraphDraftDependency(FrozenModel):
    """Provider-proposed dependency referencing local draft keys only."""

    prerequisite_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    dependent_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")


class AgentGraphDraft(FrozenModel):
    """Untrusted bounded provider output that still needs deterministic compilation."""

    nodes: tuple[AgentGraphDraftNode, ...] = Field(min_length=1, max_length=64)
    dependencies: tuple[AgentGraphDraftDependency, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def require_unique_keys(self) -> AgentGraphDraft:
        """Reject duplicate nodes and unknown dependency keys before compilation."""
        keys = tuple(node.node_key for node in self.nodes)
        if len(keys) != len(set(keys)):
            raise ValueError("Agent graph draft keys must be unique")
        known = set(keys)
        if any(
            dependency.prerequisite_key not in known or dependency.dependent_key not in known
            for dependency in self.dependencies
        ):
            raise ValueError("Agent graph draft dependency references an unknown key")
        return self


class AgentPlanningRequest(FrozenModel):
    """Minimal explicit payload approved for a task-planning provider call."""

    task_id: UUID
    user_goal: str = Field(min_length=1, max_length=4_000, repr=False)
    goal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    allowed_domains: tuple[TaskDomain, ...]
    allowed_roles: tuple[AgentRole, ...]
    max_nodes: int = Field(ge=1, le=64)
    max_depth: int = Field(ge=1, le=12)

    @model_validator(mode="after")
    def require_exact_goal_and_unique_scope(self) -> AgentPlanningRequest:
        """Bind the request to the exact goal and reject ambiguous allow-lists."""
        if hashlib.sha256(self.user_goal.encode()).hexdigest() != self.goal_digest:
            raise ValueError("Agent planning goal digest mismatch")
        if not self.allowed_domains or len(self.allowed_domains) != len(set(self.allowed_domains)):
            raise ValueError("Agent planning domains must be non-empty and unique")
        if not self.allowed_roles or len(self.allowed_roles) != len(set(self.allowed_roles)):
            raise ValueError("Agent planning roles must be non-empty and unique")
        return self


class AgentPlanningResult(FrozenModel):
    """Validated but untrusted provider proposal and trace metadata."""

    draft: AgentGraphDraft
    provider: str
    request_id: str | None = None
    prompt_version: str = Field(min_length=1, max_length=80)


class AgentLLMProvider(Protocol):
    """Replaceable provider that can only propose a task graph draft."""

    @property
    def name(self) -> str:
        """Return a stable adapter identifier."""
        ...

    async def create_task_graph(self, request: AgentPlanningRequest) -> AgentPlanningResult:
        """Return a schema-validated draft without executing or authorizing it."""
        ...
