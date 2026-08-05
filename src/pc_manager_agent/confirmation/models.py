"""Confirmation request and state models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class ConfirmationKind(StrEnum):
    """Two confirmation gates used by the application."""

    PLAN = "PLAN"
    RUNTIME = "RUNTIME"


class ConfirmationState(StrEnum):
    """Lifecycle state for an immutable confirmation request."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ConfirmationRequest(BaseModel):
    """Object-specific request bound to an immutable plan snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    confirmation_id: UUID = Field(default_factory=uuid4)
    kind: ConfirmationKind
    plan_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    step_id: str | None = None
    arguments_digest: str | None = None
    object_summary: str = Field(min_length=1, max_length=2_000)
    expires_at: datetime
    state: ConfirmationState = ConfirmationState.PENDING
