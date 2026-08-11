"""Immutable Undo and Rollback models for Stage 2A transactions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.file_operations import FileState, OperationPreviewIssue, OperationType
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel


class UndoStatus(StrEnum):
    """Persistence state of one write-ahead Undo record."""

    PREPARED = "PREPARED"
    AVAILABLE = "AVAILABLE"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"


class UndoRecord(FrozenModel):
    """Write-ahead data required to verify and reverse one completed mutation."""

    undo_id: UUID = Field(default_factory=uuid4)
    operation_id: UUID
    transaction_id: UUID
    sequence: int = Field(ge=0)
    operation_type: OperationType
    original_path: Path | None
    resulting_path: Path
    before_state: FileState | None
    after_state: FileState | None = None
    rollback_level: RollbackLevel = RollbackLevel.FULL
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: UndoStatus = UndoStatus.PREPARED
    valid_when: tuple[str, ...] = ()
    rollback_result: str | None = None

    def canonical_digest(self) -> str:
        """Return an integrity checksum used to detect accidental record corruption."""
        payload = self.model_dump(mode="json", exclude={"rollback_result"})
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class RollbackItemStatus(StrEnum):
    """Whether one Undo record can be safely executed now."""

    READY = "READY"
    CONFLICT = "CONFLICT"
    BLOCKED = "BLOCKED"


class RollbackPreviewItem(FrozenModel):
    """Live rollback assessment derived only from a persisted Undo record."""

    undo_id: UUID
    operation_id: UUID
    sequence: int = Field(ge=0)
    status: RollbackItemStatus
    current_path: Path
    restore_path: Path | None
    current_state: FileState | None = None
    issues: tuple[OperationPreviewIssue, ...] = ()


class RollbackPlan(FrozenModel):
    """Reverse-ordered rollback plan constructed from persistent Undo records."""

    rollback_plan_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    items: tuple[RollbackPreviewItem, ...]
    risk_level: RiskLevel = RiskLevel.R1
    requires_confirmation: bool = True

    def canonical_digest(self) -> str:
        """Bind rollback confirmation to exact paths, order, and conflict states."""
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
