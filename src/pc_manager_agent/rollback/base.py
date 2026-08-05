"""Truthful rollback records and command-style operation contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from pc_manager_agent.domain.risk import RollbackLevel


class UndoRecord(BaseModel):
    """Persistent metadata required to evaluate and attempt rollback."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: UUID = Field(default_factory=uuid4)
    original_path: Path | None = None
    new_path: Path | None = None
    file_identifier: str | None = None
    before_metadata: dict[str, str | int | bool | None] = Field(default_factory=dict)
    after_metadata: dict[str, str | int | bool | None] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    rollback_level: RollbackLevel
    valid_when: tuple[str, ...] = ()
    rollback_result: str | None = None


class OperationCommand(ABC):
    """Required shape for future deterministic write operations."""

    @abstractmethod
    def execute(self) -> None:
        """Execute after safety and confirmation gates."""
        raise NotImplementedError

    @abstractmethod
    def verify(self) -> bool:
        """Verify declared postconditions."""
        raise NotImplementedError

    @abstractmethod
    def build_undo_record(self) -> UndoRecord:
        """Build a truthful undo record from observed state."""
        raise NotImplementedError

    @abstractmethod
    def rollback(self, record: UndoRecord) -> bool:
        """Attempt rollback only while record validity conditions hold."""
        raise NotImplementedError
