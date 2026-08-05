"""Immutable structured task-plan models."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel


class FrozenModel(BaseModel):
    """Base model that rejects unknown fields and mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskScope(FrozenModel):
    """Paths explicitly included in and excluded from a task."""

    included_paths: tuple[Path, ...]
    excluded_paths: tuple[Path, ...] = ()

    @model_validator(mode="after")
    def require_included_path(self) -> Self:
        """Reject plans without an explicit positive scope."""
        if not self.included_paths:
            msg = "At least one included path is required"
            raise ValueError(msg)
        return self


class PlanStep(FrozenModel):
    """One deterministic registered-tool invocation."""

    step_id: str = Field(pattern=r"^step-[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    description: str = Field(min_length=1, max_length=500)
    arguments: dict[str, JsonValue]
    risk_level: RiskLevel
    requires_confirmation: bool
    rollback_level: RollbackLevel
    preconditions: tuple[str, ...] = ()
    expected_postconditions: tuple[str, ...] = ()

    def arguments_digest(self) -> str:
        """Return a stable digest used to bind runtime confirmation."""
        payload = json.dumps(
            self.arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class EstimatedImpact(FrozenModel):
    """Conservative impact estimate for a proposed plan."""

    files_read: int | None = Field(default=None, ge=0)
    files_modified: int = Field(default=0, ge=0)
    files_deleted: int = Field(default=0, ge=0)


class TaskPlan(FrozenModel):
    """Validated plan that is reviewed and confirmed before execution."""

    plan_id: UUID = Field(default_factory=uuid4)
    plan_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: str = Field(min_length=1, max_length=500)
    user_goal: str = Field(min_length=1, max_length=2_000)
    assumptions: tuple[str, ...] = ()
    scope: TaskScope
    steps: tuple[PlanStep, ...]
    estimated_impact: EstimatedImpact = Field(default_factory=EstimatedImpact)
    requires_plan_confirmation: bool = True
    requires_runtime_confirmation: bool = False

    @model_validator(mode="after")
    def validate_steps(self) -> Self:
        """Require a non-empty plan and unique step identifiers."""
        if not self.steps:
            msg = "At least one plan step is required"
            raise ValueError(msg)
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            msg = "Plan step identifiers must be unique"
            raise ValueError(msg)
        return self

    def canonical_digest(self) -> str:
        """Hash every execution-relevant plan field for confirmation binding."""
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
