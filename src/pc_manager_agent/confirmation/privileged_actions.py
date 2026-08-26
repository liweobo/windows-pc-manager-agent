"""Durable two-tier confirmations for one exact privileged protocol request."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.privileged_actions import (
    PrivilegedActionPlan,
    PrivilegedActionPreview,
    PrivilegedActionType,
    PrivilegeRequirement,
    PrivilegeResolutionStatus,
    Sha256Digest,
)
from pc_manager_agent.domain.risk import RiskLevel


class PrivilegedConfirmationTier(StrEnum):
    """Plan and immediate runtime authorization gates."""

    PLAN = "PLAN"
    RUNTIME = "RUNTIME"


class PrivilegedConfirmationState(StrEnum):
    """Durable single-use confirmation lifecycle."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


class PrivilegedActionConfirmation(FrozenModel):
    """Approval bound to action, target, plan, Preview, risk, privilege, and expiry."""

    confirmation_id: UUID = Field(default_factory=uuid4)
    parent_confirmation_id: UUID | None = None
    tier: PrivilegedConfirmationTier
    action_type: PrivilegedActionType
    plan_id: UUID
    preview_id: UUID
    plan_hash: Sha256Digest
    preview_hash: Sha256Digest
    payload_digest: Sha256Digest
    target_identity_hash: Sha256Digest
    object_summary_digest: Sha256Digest
    risk_level: RiskLevel
    privilege_requirement: PrivilegeRequirement
    requested_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime
    state: PrivilegedConfirmationState = PrivilegedConfirmationState.PENDING

    @model_validator(mode="after")
    def validate_confirmation(self) -> PrivilegedActionConfirmation:
        """Enforce tier structure, R3 semantics, and unambiguous UTC lifetime."""
        for label, value in (
            ("requested", self.requested_at),
            ("expires", self.expires_at),
            ("confirmed", self.confirmed_at),
        ):
            if value is not None and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
                raise ValueError(f"Privileged confirmation {label} time must be UTC")
        if self.expires_at <= self.requested_at:
            raise ValueError("Privileged confirmation expiry must follow its request time")
        if (self.tier is PrivilegedConfirmationTier.PLAN) != (self.parent_confirmation_id is None):
            raise ValueError("Only runtime confirmations bind a parent confirmation")
        if self.risk_level is not RiskLevel.R3:
            raise ValueError("Privileged confirmation risk must remain R3")
        if self.privilege_requirement is not PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED:
            raise ValueError("Privileged confirmation requires Administrator evidence")
        if self.state is PrivilegedConfirmationState.APPROVED and self.confirmed_at is None:
            raise ValueError("Approved confirmation requires a decision time")
        return self


class PrivilegedConfirmationError(RuntimeError):
    """Raised for stale, expired, mismatched, or replayed privileged approval."""


class PrivilegedConfirmationRepository(Protocol):
    """Persistence boundary required by the confirmation service."""

    def save_confirmation(self, confirmation: PrivilegedActionConfirmation) -> None:
        """Persist a new confirmation and its transaction transition."""
        ...

    def get_confirmation(self, confirmation_id: UUID) -> PrivilegedActionConfirmation:
        """Load one durable confirmation."""
        ...

    def update_confirmation(self, confirmation: PrivilegedActionConfirmation) -> None:
        """Persist one valid confirmation state transition."""
        ...

    def bind_runtime_preview(
        self,
        plan: PrivilegedActionPlan,
        preview: PrivilegedActionPreview,
    ) -> None:
        """Replace the Preview binding with freshly reviewed runtime evidence."""
        ...


class PrivilegedActionConfirmationService:
    """Issue and resolve durable approvals without dispatching any Broker request."""

    def __init__(
        self,
        repository: PrivilegedConfirmationRepository,
        *,
        plan_ttl_seconds: int = 300,
        runtime_ttl_seconds: int = 60,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if plan_ttl_seconds <= 0 or runtime_ttl_seconds <= 0:
            raise ValueError("Privileged confirmation TTLs must be positive")
        self._repository = repository
        self._plan_ttl = plan_ttl_seconds
        self._runtime_ttl = runtime_ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))

    def request_plan(
        self,
        plan: PrivilegedActionPlan,
        preview: PrivilegedActionPreview,
    ) -> PrivilegedActionConfirmation:
        """Persist a plan gate only for a fresh Administrator-required Mock Preview."""
        self._require_current(plan, preview)
        value = self._create(PrivilegedConfirmationTier.PLAN, plan, preview, self._plan_ttl)
        self._repository.save_confirmation(value)
        return value

    def resolve_plan(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: PrivilegedActionPlan,
        preview: PrivilegedActionPreview,
    ) -> PrivilegedActionConfirmation:
        """Resolve exactly one pending plan confirmation."""
        return self._resolve(confirmation_id, approved, plan, preview)

    def request_runtime(
        self,
        plan_confirmation_id: UUID,
        plan: PrivilegedActionPlan,
        preview: PrivilegedActionPreview,
    ) -> PrivilegedActionConfirmation:
        """Persist a short-lived child gate after a fresh equivalent Preview."""
        parent = self._repository.get_confirmation(plan_confirmation_id)
        self._require_not_expired(parent)
        if (
            parent.tier is not PrivilegedConfirmationTier.PLAN
            or parent.state is not PrivilegedConfirmationState.APPROVED
            or parent.plan_id != plan.plan_id
            or parent.plan_hash != plan.canonical_digest()
            or parent.action_type is not plan.action_type
            or parent.target_identity_hash != plan.target_identity_hash
            or parent.payload_digest != plan.payload_digest
            or parent.risk_level is not plan.risk_level
            or parent.privilege_requirement is not plan.privilege_requirement
        ):
            raise PrivilegedConfirmationError("Approved plan confirmation is stale")
        self._require_current(plan, preview)
        self._repository.bind_runtime_preview(plan, preview)
        value = self._create(
            PrivilegedConfirmationTier.RUNTIME,
            plan,
            preview,
            self._runtime_ttl,
            parent_confirmation_id=parent.confirmation_id,
        )
        self._repository.save_confirmation(value)
        return value

    def resolve_runtime(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: PrivilegedActionPlan,
        preview: PrivilegedActionPreview,
    ) -> PrivilegedActionConfirmation:
        """Resolve the immediate object-specific confirmation."""
        return self._resolve(confirmation_id, approved, plan, preview)

    def require_approved_pair(
        self,
        plan_confirmation_id: UUID,
        runtime_confirmation_id: UUID,
        plan: PrivilegedActionPlan,
        preview: PrivilegedActionPreview,
    ) -> tuple[PrivilegedActionConfirmation, PrivilegedActionConfirmation]:
        """Validate but do not consume the pair before request construction."""
        parent = self._repository.get_confirmation(plan_confirmation_id)
        runtime = self._repository.get_confirmation(runtime_confirmation_id)
        self._require_not_expired(parent)
        self._require_not_expired(runtime)
        self._require_current(plan, preview)
        if (
            parent.tier is not PrivilegedConfirmationTier.PLAN
            or runtime.tier is not PrivilegedConfirmationTier.RUNTIME
            or parent.state is not PrivilegedConfirmationState.APPROVED
            or runtime.state is not PrivilegedConfirmationState.APPROVED
            or runtime.parent_confirmation_id != parent.confirmation_id
            or runtime.plan_id != plan.plan_id
            or runtime.preview_id != preview.preview_id
            or runtime.preview_hash != preview.canonical_digest()
            or runtime.payload_digest != plan.payload_digest
            or runtime.target_identity_hash != plan.target_identity_hash
            or runtime.object_summary_digest != plan.object_summary_digest
            or runtime.risk_level is not plan.risk_level
            or runtime.privilege_requirement is not plan.privilege_requirement
        ):
            raise PrivilegedConfirmationError("Privileged confirmation pair is mismatched")
        return parent, runtime

    def _resolve(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: PrivilegedActionPlan,
        preview: PrivilegedActionPreview,
    ) -> PrivilegedActionConfirmation:
        value = self._repository.get_confirmation(confirmation_id)
        if value.state is not PrivilegedConfirmationState.PENDING:
            raise PrivilegedConfirmationError("Privileged confirmation is already resolved")
        self._require_not_expired(value)
        self._require_current(plan, preview)
        if (
            value.plan_id != plan.plan_id
            or value.plan_hash != plan.canonical_digest()
            or value.preview_id != preview.preview_id
            or value.preview_hash != preview.canonical_digest()
            or value.action_type is not plan.action_type
            or value.target_identity_hash != plan.target_identity_hash
            or value.payload_digest != plan.payload_digest
            or value.risk_level is not plan.risk_level
        ):
            raise PrivilegedConfirmationError("Privileged confirmation bindings changed")
        resolved = value.model_copy(
            update={
                "state": (
                    PrivilegedConfirmationState.APPROVED
                    if approved
                    else PrivilegedConfirmationState.REJECTED
                ),
                "confirmed_at": self._now() if approved else None,
            }
        )
        self._repository.update_confirmation(resolved)
        return resolved

    def _create(
        self,
        tier: PrivilegedConfirmationTier,
        plan: PrivilegedActionPlan,
        preview: PrivilegedActionPreview,
        ttl_seconds: int,
        *,
        parent_confirmation_id: UUID | None = None,
    ) -> PrivilegedActionConfirmation:
        now = self._now()
        return PrivilegedActionConfirmation(
            parent_confirmation_id=parent_confirmation_id,
            tier=tier,
            action_type=plan.action_type,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            plan_hash=plan.canonical_digest(),
            preview_hash=preview.canonical_digest(),
            payload_digest=plan.payload_digest,
            target_identity_hash=plan.target_identity_hash,
            object_summary_digest=plan.object_summary_digest,
            risk_level=plan.risk_level,
            privilege_requirement=plan.privilege_requirement,
            requested_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )

    def _require_not_expired(self, value: PrivilegedActionConfirmation) -> None:
        if self._now() >= value.expires_at:
            if value.state in {
                PrivilegedConfirmationState.PENDING,
                PrivilegedConfirmationState.APPROVED,
            }:
                self._repository.update_confirmation(
                    value.model_copy(update={"state": PrivilegedConfirmationState.EXPIRED})
                )
            raise PrivilegedConfirmationError("Privileged confirmation expired")

    @staticmethod
    def _require_current(
        plan: PrivilegedActionPlan,
        preview: PrivilegedActionPreview,
    ) -> None:
        resolution = preview.privilege_resolution
        if (
            preview.plan_id != plan.plan_id
            or preview.plan_hash != plan.canonical_digest()
            or preview.action_type is not plan.action_type
            or preview.target_identity_hash != plan.target_identity_hash
            or preview.risk_level is not plan.risk_level
            or not preview.mock_only
            or resolution.status is not PrivilegeResolutionStatus.REQUIRED
            or resolution.requirement is not PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED
            or not resolution.safety_allowed
        ):
            raise PrivilegedConfirmationError("Privileged Preview is blocked or stale")
