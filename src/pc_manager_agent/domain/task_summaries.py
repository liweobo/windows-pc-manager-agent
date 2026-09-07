"""Deterministic task summary and domain-specific recovery aggregation."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, model_validator

from pc_manager_agent.domain.computer_tasks import ComputerTaskState
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.task_workflows import DomainRecoverySummary


class StructuredTaskSummary(FrozenModel):
    """Facts derived from domain receipts; model prose cannot change these values."""

    task_id: UUID
    state: ComputerTaskState
    completed_verified: int = Field(default=0, ge=0)
    completed_unverified: int = Field(default=0, ge=0)
    partial: int = Field(default=0, ge=0)
    blocked: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)
    changed_count: int = Field(default=0, ge=0)
    unchanged_count: int = Field(default=0, ge=0)
    fact_codes: tuple[str, ...] = Field(default=(), max_length=64)
    recovery: tuple[DomainRecoverySummary, ...] = Field(default=(), max_length=64)
    global_undo_available: bool = False

    @model_validator(mode="after")
    def prohibit_global_undo_and_duplicate_facts(self) -> StructuredTaskSummary:
        """V1 never claims a cross-domain atomic Undo."""
        if self.global_undo_available:
            raise ValueError("Global task Undo is forbidden")
        if len(self.fact_codes) != len(set(self.fact_codes)):
            raise ValueError("Task summary fact codes must be unique")
        return self
