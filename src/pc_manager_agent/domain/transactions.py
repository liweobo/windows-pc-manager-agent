"""Persistent operation transaction and item state models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from pydantic import Field

from pc_manager_agent.domain.file_operations import OperationType
from pc_manager_agent.domain.plans import FrozenModel


class TransactionState(StrEnum):
    """Explicit lifecycle for a durable Stage 2 file-operation transaction."""

    PLANNED = "PLANNED"
    PREVIEWED = "PREVIEWED"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    AWAITING_RUNTIME_CONFIRMATION = "AWAITING_RUNTIME_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    PARTIALLY_ROLLED_BACK = "PARTIALLY_ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"


class OperationItemState(StrEnum):
    """Persistent lifecycle for one operation inside a transaction."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    SKIPPED = "SKIPPED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"


class FailurePolicy(StrEnum):
    """Batch behavior after an operation fails."""

    STOP_ON_UNEXPECTED_ERROR = "STOP_ON_UNEXPECTED_ERROR"


class OperationTransaction(FrozenModel):
    """Durable summary of one confirmed mutation batch."""

    transaction_id: UUID
    plan_id: UUID
    preview_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    preview_digest: str = Field(min_length=64, max_length=64)
    state: TransactionState
    failure_policy: FailurePolicy = FailurePolicy.STOP_ON_UNEXPECTED_ERROR
    operation_count: int = Field(ge=1)
    ready_count: int = Field(ge=0)
    completed_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    skipped_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    confirmation_id: UUID | None = None
    confirmed_at: datetime | None = None
    error_message: str | None = Field(default=None, max_length=2_000)


class TransactionItem(FrozenModel):
    """Durable status and routing data for one planned mutation."""

    transaction_id: UUID
    operation_id: UUID
    sequence: int = Field(ge=0)
    operation_type: OperationType
    tool_name: str
    source: Path | None
    destination: Path
    state: OperationItemState
    error_code: str | None = None
    error_message: str | None = Field(default=None, max_length=2_000)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class OperationProgress(FrozenModel):
    """Thread-safe immutable progress payload emitted between atomic operations."""

    transaction_id: UUID
    current_sequence: int = Field(ge=0)
    operation_count: int = Field(ge=1)
    completed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    current_path: Path | None = None


class OperationExecutionReport(FrozenModel):
    """Verified terminal transaction result shown to the user and audit log."""

    transaction: OperationTransaction
    items: tuple[TransactionItem, ...]
    rollback_available_count: int = Field(ge=0)
    total_size_bytes: int = Field(ge=0)
