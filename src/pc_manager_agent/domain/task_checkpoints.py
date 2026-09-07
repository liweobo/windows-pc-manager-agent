"""Content-minimized task checkpoints and exactly-once orchestration dispatch state."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.task_graph import TaskDependency, TaskNode, TaskNodeStatus
from pc_manager_agent.domain.task_workflows import DomainType


class TaskDispatchState(StrEnum):
    """One-way dispatch lifecycle; restart never resets a write to NOT_DISPATCHED."""

    NOT_DISPATCHED = "NOT_DISPATCHED"
    DISPATCHING = "DISPATCHING"
    DISPATCHED = "DISPATCHED"
    RESULT_RECEIVED = "RESULT_RECEIVED"
    RECONCILING = "RECONCILING"
    RESOLVED = "RESOLVED"


class PersistedTaskGraph(FrozenModel):
    """Recoverable graph structure without the raw user goal or execution authority."""

    graph_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    version: int = Field(ge=1)
    goal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    graph_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    nodes: tuple[TaskNode, ...]
    dependencies: tuple[TaskDependency, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def require_exact_graph_membership(self) -> PersistedTaskGraph:
        """Reject unknown dependency endpoints and duplicate nodes."""
        node_ids = tuple(node.node_id for node in self.nodes)
        if not node_ids or len(node_ids) != len(set(node_ids)):
            raise ValueError("Persisted graph requires unique nodes")
        known = set(node_ids)
        if any(
            edge.prerequisite_id not in known or edge.dependent_id not in known
            for edge in self.dependencies
        ):
            raise ValueError("Persisted graph dependency references an unknown node")
        return self


class TaskNodeSnapshot(FrozenModel):
    """Durable node metadata without Context, target data, or confirmation authority."""

    node_id: UUID
    domain: DomainType | None
    status: TaskNodeStatus
    attempt_count: int = Field(default=0, ge=0, le=3)
    result_ref: str | None = Field(default=None, max_length=200)
    domain_transaction_ref: str | None = Field(default=None, max_length=200)
    error_code: str | None = Field(default=None, max_length=100)


class TaskCheckpoint(FrozenModel):
    """Durable progress evidence that never functions as authorization."""

    checkpoint_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    graph_id: UUID
    graph_version: int = Field(ge=1)
    graph_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    nodes: tuple[TaskNodeSnapshot, ...]
    completed_node_ids: tuple[UUID, ...] = ()
    current_node_id: UUID | None = None
    node_result_refs: tuple[str, ...] = Field(default=(), max_length=64)
    dispatch_ids: tuple[UUID, ...] = Field(default=(), max_length=64)
    pending_attention_ids: tuple[UUID, ...] = Field(default=(), max_length=64)
    invalidated_confirmation_count: int = Field(default=0, ge=0, le=64)
    checkpoint_reason: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,99}$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    authorization_restored: bool = False

    @model_validator(mode="after")
    def require_unique_content_free_state(self) -> TaskCheckpoint:
        """Reject duplicate references and any claim that authority was restored."""
        if self.authorization_restored:
            raise ValueError("Checkpoint cannot restore authorization")
        collections = (
            tuple(item.node_id for item in self.nodes),
            self.completed_node_ids,
            self.node_result_refs,
            self.dispatch_ids,
            self.pending_attention_ids,
        )
        if any(len(values) != len(set(values)) for values in collections):
            raise ValueError("Checkpoint collections must be unique")
        if not set(self.completed_node_ids).issubset({item.node_id for item in self.nodes}):
            raise ValueError("Checkpoint completed nodes are unknown")
        return self


class TaskNodeDispatch(FrozenModel):
    """Durable single-dispatch identity for one graph-version node."""

    dispatch_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    graph_version: int = Field(ge=1)
    node_id: UUID
    domain: DomainType | None
    state: TaskDispatchState = TaskDispatchState.NOT_DISPATCHED
    read_only: bool
    attempt_count: int = Field(default=0, ge=0, le=3)
    domain_transaction_ref: str | None = Field(default=None, max_length=200)
    result_ref: str | None = Field(default=None, max_length=200)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def require_consistent_dispatch(self) -> TaskNodeDispatch:
        """Reject backwards timestamps and write retries."""
        if self.updated_at < self.created_at:
            raise ValueError("Dispatch update cannot precede creation")
        if not self.read_only and self.attempt_count > 1:
            raise ValueError("Write-capable nodes cannot be retried")
        return self
