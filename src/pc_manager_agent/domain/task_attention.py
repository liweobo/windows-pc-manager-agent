"""Serialized, metadata-only user attention queue models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.task_workflows import DomainType


class UserAttentionKind(StrEnum):
    """Finite reasons a task may legitimately wait for its user."""

    PLAN_CONFIRMATION = "PLAN_CONFIRMATION"
    DOMAIN_CONFIRMATION = "DOMAIN_CONFIRMATION"
    TARGET_SELECTION = "TARGET_SELECTION"
    USER_TAKEOVER = "USER_TAKEOVER"
    FILE_CONFLICT = "FILE_CONFLICT"
    RECOVERY_DECISION = "RECOVERY_DECISION"
    SCOPE_EXPANSION = "SCOPE_EXPANSION"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class UserAttentionState(StrEnum):
    """Attention lifecycle; only one high-risk item may be active."""

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"
    INVALIDATED = "INVALIDATED"


class UserAttentionItem(FrozenModel):
    """Safe UI prompt metadata with no confirmation token or sensitive payload."""

    attention_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    node_id: UUID | None = None
    graph_version: int = Field(ge=1)
    kind: UserAttentionKind
    state: UserAttentionState = UserAttentionState.PENDING
    domain: DomainType | None = None
    risk_level: RiskLevel
    title_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,79}$")
    summary: str = Field(min_length=1, max_length=500)
    object_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
    notification_can_authorize: bool = False

    @model_validator(mode="after")
    def require_safe_attention_state(self) -> UserAttentionItem:
        """Notifications never authorize and resolved timestamps match state."""
        if self.notification_can_authorize:
            raise ValueError("Notification and attention items cannot authorize actions")
        resolved = self.state in {
            UserAttentionState.RESOLVED,
            UserAttentionState.DISMISSED,
            UserAttentionState.INVALIDATED,
        }
        if resolved != (self.resolved_at is not None):
            raise ValueError("Attention resolution timestamp is inconsistent")
        return self
