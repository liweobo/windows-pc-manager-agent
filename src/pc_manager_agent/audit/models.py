"""Structured audit event model."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from pc_manager_agent.domain.risk import RiskLevel


class AuditEvent(BaseModel):
    """One redacted, append-only audit event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_type: str
    original_request: str | None = None
    plan: dict[str, JsonValue] | None = None
    plan_id: str | None = None
    plan_version: int | None = None
    agent_decision: str | None = None
    step_id: str | None = None
    tool_name: str | None = None
    parameters: dict[str, JsonValue] | None = None
    risk_level: RiskLevel | None = None
    confirmation_required: bool = False
    confirmation_result: str | None = None
    before_state: dict[str, JsonValue] | None = None
    result: dict[str, JsonValue] | None = None
    after_state: dict[str, JsonValue] | None = None
    error: dict[str, JsonValue] | None = None
    rollback: dict[str, JsonValue] | None = None
    verification: dict[str, JsonValue] | None = None
    model_provider: str | None = None
    model_request_id: str | None = None
    app_version: str
    git_commit: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
