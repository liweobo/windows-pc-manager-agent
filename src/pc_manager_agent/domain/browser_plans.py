"""Digest-bound structured plans and confirmations for browser actions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.browser import BrowserActionRequest, BrowserPolicyResult
from pc_manager_agent.domain.plans import FrozenModel


class BrowserConfirmationState(StrEnum):
    """Durable single-use confirmation lifecycle."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


class BrowserTaskPlan(FrozenModel):
    """One exact browser action plan with no selector, script, or generic argument map."""

    plan_id: UUID = Field(default_factory=uuid4)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: str = Field(min_length=1, max_length=500)
    user_goal_summary: str = Field(min_length=1, max_length=500)
    action: BrowserActionRequest
    policy: BrowserPolicyResult
    allowed_origin: str = Field(min_length=1, max_length=512)
    expected_effect: str = Field(min_length=1, max_length=500)
    download_preview_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    profile_persistence: bool = False
    model_authority: bool = False
    requires_plan_confirmation: bool = True

    def canonical_digest(self) -> str:
        """Hash all execution-relevant fields for stale-plan rejection."""
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class BrowserConfirmationRecord(FrozenModel):
    """Durable approval bound to one session, page generation, plan, action, and expiry."""

    confirmation_id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    session_id: UUID
    page_id: UUID | None
    navigation_id: UUID | None
    plan_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    action_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    origin: str = Field(min_length=1, max_length=512)
    state: BrowserConfirmationState = BrowserConfirmationState.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
