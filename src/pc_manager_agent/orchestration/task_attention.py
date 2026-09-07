"""Serialized user-attention queue for task, domain, and recovery decisions."""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from uuid import UUID

from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.task_attention import (
    UserAttentionItem,
    UserAttentionState,
)
from pc_manager_agent.persistence.computer_tasks import ComputerTaskRepository


class TaskAttentionError(RuntimeError):
    """Raised when attention resolution is stale or could confuse authorization."""


class UserAttentionQueue:
    """Persist prompts and serialize active R2/R3 review across tasks."""

    def __init__(self, repository: ComputerTaskRepository) -> None:
        self._repository = repository

    def enqueue(self, item: UserAttentionItem) -> UserAttentionItem:
        """Persist one pending item and activate it when presentation is safe."""
        self._repository.save_attention(item)
        return self.activate_next()

    def activate_next(self) -> UserAttentionItem:
        """Activate the oldest presentable item without approving any operation."""
        items = self._repository.list_all_attention()
        active = next((item for item in items if item.state is UserAttentionState.ACTIVE), None)
        high_risk_active = next(
            (
                item
                for item in items
                if item.state is UserAttentionState.ACTIVE
                and item.risk_level.severity >= RiskLevel.R2.severity
            ),
            None,
        )
        if active is not None:
            return active
        pending = next(
            (
                item
                for item in items
                if item.state is UserAttentionState.PENDING
                and (item.risk_level.severity < RiskLevel.R2.severity or high_risk_active is None)
            ),
            None,
        )
        if pending is None:
            raise TaskAttentionError("No attention item is ready for presentation")
        changed = pending.model_copy(update={"state": UserAttentionState.ACTIVE})
        self._repository.update_attention(changed, UserAttentionState.PENDING)
        return changed

    def resolve(
        self,
        attention_id: UUID,
        *,
        dismissed: bool = False,
        now: datetime | None = None,
    ) -> UserAttentionItem:
        """Close one UI prompt; resolution never means confirmation approval."""
        items = self._repository.list_all_attention(include_resolved=True, limit=500)
        try:
            current = next(item for item in items if item.attention_id == attention_id)
        except StopIteration as exc:
            raise TaskAttentionError("Unknown attention item") from exc
        if current.state not in {UserAttentionState.PENDING, UserAttentionState.ACTIVE}:
            raise TaskAttentionError("Attention item is already resolved")
        changed = current.model_copy(
            update={
                "state": (
                    UserAttentionState.DISMISSED if dismissed else UserAttentionState.RESOLVED
                ),
                "resolved_at": now or datetime.now(UTC),
            }
        )
        self._repository.update_attention(changed, current.state)
        with suppress(TaskAttentionError):
            self.activate_next()
        return changed

    def pending(self, task_id: UUID | None = None) -> tuple[UserAttentionItem, ...]:
        """Return unresolved metadata for one task or the global queue."""
        if task_id is None:
            return self._repository.list_all_attention()
        return self._repository.list_attention(task_id)
