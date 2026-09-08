"""Durable Stage 5E task identity, lifecycle, policy, budget, and plan consent."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.plans import FrozenModel


class ComputerTaskState(StrEnum):
    """Explicit root lifecycle; waiting states are normal and never imply failure."""

    CREATED = "CREATED"
    PLANNING = "PLANNING"
    AWAITING_PLAN_CONFIRMATION = "AWAITING_PLAN_CONFIRMATION"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    WAITING_FOR_DOMAIN_CONFIRMATION = "WAITING_FOR_DOMAIN_CONFIRMATION"
    WAITING_FOR_USER_TAKEOVER = "WAITING_FOR_USER_TAKEOVER"
    PAUSED = "PAUSED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    INTERRUPTED = "INTERRUPTED"
    RECOVERING = "RECOVERING"


class ComputerTaskKind(StrEnum):
    """Finite user-facing task categories, not executable capabilities."""

    ANALYSIS_ONLY = "ANALYSIS_ONLY"
    ANALYSIS_AND_REPORT = "ANALYSIS_AND_REPORT"
    GUIDED_ACTION = "GUIDED_ACTION"
    DOCUMENT_WORKFLOW = "DOCUMENT_WORKFLOW"
    BROWSER_RESEARCH = "BROWSER_RESEARCH"
    MIXED_COMPUTER_TASK = "MIXED_COMPUTER_TASK"


class AutonomyLevel(StrEnum):
    """V1 levels intentionally exclude unattended execution."""

    EXPLAIN_ONLY = "EXPLAIN_ONLY"
    PLAN_AND_ANALYZE = "PLAN_AND_ANALYZE"
    GUIDED_EXECUTION = "GUIDED_EXECUTION"


class DependencyType(StrEnum):
    """How a prerequisite result affects a dependent node."""

    HARD_DEPENDENCY = "HARD_DEPENDENCY"
    SOFT_DEPENDENCY = "SOFT_DEPENDENCY"
    OPTIONAL = "OPTIONAL"


class NodeFailurePolicy(StrEnum):
    """Finite failure decisions; retries are limited to safe reads."""

    STOP_TASK = "STOP_TASK"
    CONTINUE_PARTIAL = "CONTINUE_PARTIAL"
    WAIT_FOR_USER = "WAIT_FOR_USER"
    RETRY_SAFE_READ = "RETRY_SAFE_READ"


class TaskPolicySnapshot(FrozenModel):
    """Policy versions that explain how a task was originally reviewed."""

    safety_policy_version: str = Field(min_length=1, max_length=80)
    tool_registry_version: str = Field(min_length=1, max_length=80)
    agent_capability_version: str = Field(min_length=1, max_length=80)
    task_schema_version: int = Field(default=1, ge=1)
    application_version: str = Field(min_length=1, max_length=40)

    def canonical_digest(self) -> str:
        """Bind a task to the exact recorded policy versions."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


class TaskBudget(FrozenModel):
    """Immutable per-task ceilings derived from local configuration."""

    max_task_nodes: int = Field(ge=1, le=64)
    max_agent_calls: int = Field(ge=0, le=32)
    max_tool_preparations: int = Field(ge=1, le=64)
    max_browser_navigations: int = Field(ge=0, le=50)
    max_files_scanned: int = Field(ge=1, le=100_000)
    max_runtime_seconds: int = Field(ge=60, le=21_600)
    max_llm_calls: int = Field(ge=0, le=32)
    max_delegation_depth: int = Field(ge=0, le=5)


class TaskBudgetUsage(FrozenModel):
    """Monotonic observed usage; exceeding a ceiling pauses rather than weakens safety."""

    task_nodes: int = Field(default=0, ge=0)
    agent_calls: int = Field(default=0, ge=0)
    tool_preparations: int = Field(default=0, ge=0)
    browser_navigations: int = Field(default=0, ge=0)
    files_scanned: int = Field(default=0, ge=0)
    runtime_seconds: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    delegation_depth: int = Field(default=0, ge=0)

    def exceeded(self, budget: TaskBudget) -> tuple[str, ...]:
        """Return stable reason codes for every exhausted task budget."""
        pairs = (
            ("TASK_NODES", self.task_nodes, budget.max_task_nodes),
            ("AGENT_CALLS", self.agent_calls, budget.max_agent_calls),
            ("TOOL_PREPARATIONS", self.tool_preparations, budget.max_tool_preparations),
            ("BROWSER_NAVIGATIONS", self.browser_navigations, budget.max_browser_navigations),
            ("FILES_SCANNED", self.files_scanned, budget.max_files_scanned),
            ("RUNTIME", self.runtime_seconds, budget.max_runtime_seconds),
            ("LLM_CALLS", self.llm_calls, budget.max_llm_calls),
            ("DELEGATION_DEPTH", self.delegation_depth, budget.max_delegation_depth),
        )
        return tuple(f"TASK_BUDGET_EXCEEDED_{code}" for code, used, limit in pairs if used > limit)


class TaskProgress(FrozenModel):
    """Truthful planned-step and open-ended work counters without a fabricated ETA."""

    planned_steps: int = Field(ge=1, le=64)
    completed_steps: int = Field(default=0, ge=0, le=64)
    failed_steps: int = Field(default=0, ge=0, le=64)
    blocked_steps: int = Field(default=0, ge=0, le=64)
    current_phase_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,79}$")
    observed_units: int | None = Field(default=None, ge=0)
    observed_unit_name: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def require_truthful_counts(self) -> TaskProgress:
        """Reject impossible progress and incomplete open-ended counter labels."""
        if self.completed_steps + self.failed_steps + self.blocked_steps > self.planned_steps:
            raise ValueError("Task progress exceeds planned steps")
        if (self.observed_units is None) != (self.observed_unit_name is None):
            raise ValueError("Observed units and their label must be provided together")
        return self


class ComputerTask(FrozenModel):
    """Content-minimized durable root task; raw goals and domain data stay volatile."""

    task_id: UUID = Field(default_factory=uuid4)
    root_request_id: UUID
    conversation_id: UUID | None = None
    trace_id: UUID = Field(default_factory=uuid4)
    safe_goal_summary: str = Field(min_length=1, max_length=240)
    goal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_kind: ComputerTaskKind
    autonomy_level: AutonomyLevel
    state: ComputerTaskState = ComputerTaskState.CREATED
    graph_id: UUID
    graph_version: int = Field(default=1, ge=1)
    graph_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy: TaskPolicySnapshot
    budget: TaskBudget
    usage: TaskBudgetUsage = Field(default_factory=TaskBudgetUsage)
    progress: TaskProgress
    current_node_id: UUID | None = None
    pause_requested: bool = False
    cancellation_requested: bool = False
    recoverability_state: str | None = Field(default=None, max_length=80)
    schema_version: int = Field(default=1, ge=1)
    revision: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def require_consistent_lifecycle(self) -> ComputerTask:
        """Reject contradictory timestamps, versions, and completion claims."""
        if self.updated_at < self.created_at:
            raise ValueError("Task update cannot precede creation")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("Task start cannot precede creation")
        started_at = self.started_at
        if self.completed_at is not None and self.completed_at < self.created_at:
            raise ValueError("Task completion cannot precede creation")
        if (
            self.completed_at is not None
            and started_at is None
            and self.state in {ComputerTaskState.COMPLETED, ComputerTaskState.PARTIALLY_COMPLETED}
        ):
            raise ValueError("Executed completion requires a start time")
        if (
            self.completed_at is not None
            and started_at is not None
            and self.completed_at < started_at
        ):
            raise ValueError("Task completion cannot precede start")
        if self.policy.task_schema_version != self.schema_version:
            raise ValueError("Task and policy schema versions disagree")
        return self


class TaskPlanConfirmationState(StrEnum):
    """Task-plan consent never doubles as a domain-action authorization."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"
    INVALIDATED = "INVALIDATED"


class TaskPlanConfirmation(FrozenModel):
    """Single-use approval for the exact listed read-only coordination plan."""

    confirmation_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    graph_id: UUID
    graph_version: int = Field(ge=1)
    goal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    graph_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    read_only_node_ids: tuple[UUID, ...]
    state: TaskPlanConfirmationState = TaskPlanConfirmationState.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    resolved_at: datetime | None = None
    domain_write_authorized: Literal[False] = False

    @model_validator(mode="after")
    def require_live_unique_scope(self) -> TaskPlanConfirmation:
        """Reject empty, duplicate, expired-at-creation, or inconsistent consent."""
        if not self.read_only_node_ids or len(self.read_only_node_ids) != len(
            set(self.read_only_node_ids)
        ):
            raise ValueError("Task plan confirmation requires unique read-only nodes")
        if self.expires_at <= self.created_at:
            raise ValueError("Task plan confirmation expiry must be in the future")
        if self.state is TaskPlanConfirmationState.PENDING and self.resolved_at is not None:
            raise ValueError("Pending task plan confirmation cannot be resolved")
        if self.state is not TaskPlanConfirmationState.PENDING and self.resolved_at is None:
            raise ValueError("Resolved task plan confirmation needs a timestamp")
        return self

    def canonical_digest(self) -> str:
        """Bind storage and audit to this exact non-write consent."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


class TaskRevisionRequest(FrozenModel):
    """Explicit user revision; model, web, document, and Memory text cannot construct it."""

    revision_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    expected_graph_version: int = Field(ge=1)
    new_goal: str = Field(min_length=1, max_length=4_000, exclude=True, repr=False)
    requested_domain_codes: tuple[str, ...] = Field(max_length=16)
    scope_expansion_approved: bool = False
    user_initiated: Literal[True] = True
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GlobalErrorEnvelope(FrozenModel):
    """Human-readable cross-domain error without erasing the original domain code."""

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,99}$")
    message: str = Field(min_length=1, max_length=1_000)
    domain_code: str | None = Field(default=None, max_length=100)
    changed_count: int = Field(default=0, ge=0)
    unchanged_count: int = Field(default=0, ge=0)
    next_steps: tuple[str, ...] = Field(default=(), max_length=5)
    task_id: UUID | None = None
    node_id: UUID | None = None


class ComputerTaskEvent(FrozenModel):
    """Content-free task trace event correlated with the independent audit log."""

    event_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    trace_id: UUID
    graph_version: int = Field(ge=1)
    node_id: UUID | None = None
    event_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,99}$")
    detail_codes: tuple[str, ...] = Field(default=(), max_length=20)
    reference_digests: tuple[str, ...] = Field(default=(), max_length=20)
    item_count: int = Field(default=0, ge=0)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def require_safe_event_metadata(self) -> ComputerTaskEvent:
        """Reject duplicate or non-digest references in persisted task events."""
        if len(self.detail_codes) != len(set(self.detail_codes)):
            raise ValueError("Task event detail codes must be unique")
        if len(self.reference_digests) != len(set(self.reference_digests)) or any(
            len(value) != 64 for value in self.reference_digests
        ):
            raise ValueError("Task event references must be unique SHA-256 digests")
        return self
