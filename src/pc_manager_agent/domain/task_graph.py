"""Bounded task graph models; graphs express intent and never authorization."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.agents import AgentRole
from pc_manager_agent.domain.computer_tasks import DependencyType, NodeFailurePolicy
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel


class TaskDomain(StrEnum):
    """Finite coordination domains mapped to existing business boundaries."""

    GENERAL = "GENERAL"
    FILE = "FILE"
    SYSTEM = "SYSTEM"
    SOFTWARE = "SOFTWARE"
    OFFICE = "OFFICE"
    BROWSER = "BROWSER"
    OPTIMIZATION = "OPTIMIZATION"
    MEMORY = "MEMORY"
    PROCESS = "PROCESS"
    STARTUP = "STARTUP"
    SERVICE = "SERVICE"
    RESIDUAL = "RESIDUAL"
    CLEANUP = "CLEANUP"


class TaskNodeType(StrEnum):
    """Finite kinds; none means that authorization already exists."""

    UNDERSTAND = "UNDERSTAND"
    READ = "READ"
    ANALYZE = "ANALYZE"
    RESOLVE_TARGET = "RESOLVE_TARGET"
    PREPARE_ACTION = "PREPARE_ACTION"
    WAIT_FOR_CONFIRMATION = "WAIT_FOR_CONFIRMATION"
    EXECUTE_DOMAIN_ACTION = "EXECUTE_DOMAIN_ACTION"
    VERIFY = "VERIFY"
    SUMMARIZE = "SUMMARIZE"
    MEMORY_CANDIDATE = "MEMORY_CANDIDATE"


class TaskNodeStatus(StrEnum):
    """Node lifecycle without any implicit success inference."""

    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class ResourceAccess(StrEnum):
    """Scheduling intent; it does not replace a domain handle or lock."""

    READ = "READ"
    WRITE = "WRITE"


class ResourceIdentity(FrozenModel):
    """Opaque stable identity supplied by the owning domain."""

    domain: TaskDomain
    kind: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,39}$")
    identity_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    access: ResourceAccess


class TaskNode(FrozenModel):
    """One bounded work item assigned to a runtime Agent role."""

    node_id: UUID = Field(default_factory=uuid4)
    node_type: TaskNodeType
    domain: TaskDomain
    agent_role: AgentRole
    status: TaskNodeStatus = TaskNodeStatus.PENDING
    input_refs: tuple[str, ...] = Field(default=(), max_length=32)
    output_refs: tuple[str, ...] = Field(default=(), max_length=32)
    resources: tuple[ResourceIdentity, ...] = Field(default=(), max_length=32)
    risk_hint: RiskLevel = RiskLevel.R0
    failure_policy: NodeFailurePolicy = NodeFailurePolicy.STOP_TASK
    safe_read_retry_limit: int = Field(default=0, ge=0, le=2)
    roadmap_label: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def require_unique_references(self) -> TaskNode:
        """Reject reference ambiguity inside a node."""
        if len(self.input_refs) != len(set(self.input_refs)):
            raise ValueError("Input references must be unique")
        if len(self.output_refs) != len(set(self.output_refs)):
            raise ValueError("Output references must be unique")
        if self.risk_hint is not RiskLevel.R0 and self.safe_read_retry_limit:
            raise ValueError("Only R0 nodes may be retried")
        if self.failure_policy is NodeFailurePolicy.RETRY_SAFE_READ and (
            self.risk_hint is not RiskLevel.R0 or self.safe_read_retry_limit == 0
        ):
            raise ValueError("Safe-read retry policy requires a bounded R0 retry")
        return self


class TaskDependency(FrozenModel):
    """Directed dependency from a prerequisite to a dependent node."""

    prerequisite_id: UUID
    dependent_id: UUID
    dependency_type: DependencyType = DependencyType.HARD_DEPENDENCY

    @model_validator(mode="after")
    def reject_self_dependency(self) -> TaskDependency:
        """A node cannot depend on itself."""
        if self.prerequisite_id == self.dependent_id:
            raise ValueError("Self dependencies are forbidden")
        return self


class TaskGraph(FrozenModel):
    """Volatile user goal plus persistable digest-bound graph metadata."""

    task_id: UUID = Field(default_factory=uuid4)
    goal: str = Field(min_length=1, max_length=4_000, exclude=True, repr=False)
    goal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    nodes: tuple[TaskNode, ...]
    dependencies: tuple[TaskDependency, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def require_goal_and_unique_nodes(self) -> TaskGraph:
        """Bind the graph to the exact goal and reject duplicate nodes/edges."""
        expected = hashlib.sha256(self.goal.encode()).hexdigest()
        if self.goal_digest != expected:
            raise ValueError("Task goal digest mismatch")
        if not self.nodes:
            raise ValueError("Task graph requires at least one node")
        node_ids = tuple(node.node_id for node in self.nodes)
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Task node identifiers must be unique")
        edges = tuple(
            (dependency.prerequisite_id, dependency.dependent_id)
            for dependency in self.dependencies
        )
        if len(edges) != len(set(edges)):
            raise ValueError("Task dependencies must be unique")
        return self

    @classmethod
    def create(
        cls,
        goal: str,
        nodes: tuple[TaskNode, ...],
        dependencies: tuple[TaskDependency, ...] = (),
    ) -> TaskGraph:
        """Create a graph with a digest derived from the exact volatile goal."""
        return cls(
            goal=goal,
            goal_digest=hashlib.sha256(goal.encode()).hexdigest(),
            nodes=nodes,
            dependencies=dependencies,
        )

    def canonical_digest(self) -> str:
        """Hash persistable graph metadata; the goal is represented only by its digest."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()
