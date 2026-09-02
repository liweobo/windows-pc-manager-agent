"""Task result and evidence provenance models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator

from pc_manager_agent.domain.plans import FrozenModel


class TaskOutcomeStatus(StrEnum):
    """Root task terminal states."""

    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class RootTaskStatus(StrEnum):
    """Durable root-task lifecycle; active states never resume after restart."""

    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    INTERRUPTED = "INTERRUPTED"


class FactProvenance(StrEnum):
    """Evidence strength used for deterministic result precedence."""

    DOMAIN_VERIFIED = "DOMAIN_VERIFIED"
    DOMAIN_OBSERVED = "DOMAIN_OBSERVED"
    USER_SUPPLIED = "USER_SUPPLIED"
    MODEL_INFERENCE = "MODEL_INFERENCE"

    @property
    def precedence(self) -> int:
        """Return a fixed order where deterministic verification is strongest."""
        return {
            FactProvenance.DOMAIN_VERIFIED: 4,
            FactProvenance.DOMAIN_OBSERVED: 3,
            FactProvenance.USER_SUPPLIED: 2,
            FactProvenance.MODEL_INFERENCE: 1,
        }[self]


class TaskFact(FrozenModel):
    """One bounded result fact with explicit provenance."""

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,79}$")
    summary: str = Field(min_length=1, max_length=500)
    provenance: FactProvenance
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=32)


class TaskOutcome(FrozenModel):
    """User-facing aggregate that cannot override domain verification."""

    task_id: UUID
    status: TaskOutcomeStatus
    facts: tuple[TaskFact, ...] = ()
    completed_node_ids: tuple[UUID, ...] = ()
    incomplete_node_ids: tuple[UUID, ...] = ()
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,79}$")

    @model_validator(mode="after")
    def require_disjoint_nodes(self) -> TaskOutcome:
        """Prevent a node from being reported both complete and incomplete."""
        if set(self.completed_node_ids) & set(self.incomplete_node_ids):
            raise ValueError("Completed and incomplete node sets must be disjoint")
        return self


class TaskJournalEntry(FrozenModel):
    """Content-free durable summary used for task status and restart truth."""

    task_id: UUID
    graph_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    goal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: RootTaskStatus
    node_count: int = Field(ge=1, le=64)
    completed_count: int = Field(default=0, ge=0, le=64)
    blocked_count: int = Field(default=0, ge=0, le=64)
    agent_roles: tuple[str, ...]
    revision: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def require_consistent_counts(self) -> TaskJournalEntry:
        """Reject summaries that claim more terminal nodes than exist."""
        if self.completed_count + self.blocked_count > self.node_count:
            raise ValueError("Task journal counts exceed node count")
        if len(self.agent_roles) != len(set(self.agent_roles)):
            raise ValueError("Task journal Agent roles must be unique")
        if self.updated_at < self.created_at:
            raise ValueError("Task journal update cannot precede creation")
        return self
