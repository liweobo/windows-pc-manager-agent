"""Truthful rollback records and command-style operation contract."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pc_manager_agent.rollback.models import UndoRecord


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
