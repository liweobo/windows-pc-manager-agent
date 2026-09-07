"""High-level domain handoff, result, reconciliation, and recovery models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel


class DomainType(StrEnum):
    """Finite high-level domains visible to the Final Orchestrator."""

    FILE = "FILE"
    SYSTEM = "SYSTEM"
    PROCESS = "PROCESS"
    STARTUP = "STARTUP"
    SERVICE = "SERVICE"
    SOFTWARE = "SOFTWARE"
    RESIDUAL = "RESIDUAL"
    CLEANUP = "CLEANUP"
    OFFICE = "OFFICE"
    BROWSER = "BROWSER"
    OPTIMIZATION = "OPTIMIZATION"


class DomainPreparationStatus(StrEnum):
    """A preparation result is never an execution result."""

    READY_FOR_READ = "READY_FOR_READ"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    NEEDS_TARGET_SELECTION = "NEEDS_TARGET_SELECTION"
    NEEDS_DOMAIN_CONFIRMATION = "NEEDS_DOMAIN_CONFIRMATION"
    NEEDS_USER_TAKEOVER = "NEEDS_USER_TAKEOVER"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class DomainResultStatus(StrEnum):
    """Unified status preserving verified and unverified outcomes."""

    COMPLETED_VERIFIED = "COMPLETED_VERIFIED"
    COMPLETED_UNVERIFIED = "COMPLETED_UNVERIFIED"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DomainVerificationStatus(StrEnum):
    """Whether the owning domain established its declared postcondition."""

    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DomainReconciliationStatus(StrEnum):
    """Fresh restart observations without an automatic replay decision."""

    CONFIRMED_COMPLETED = "CONFIRMED_COMPLETED"
    CONFIRMED_NOT_EXECUTED = "CONFIRMED_NOT_EXECUTED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    STATE_CHANGED = "STATE_CHANGED"
    UNVERIFIED = "UNVERIFIED"
    REQUIRES_MANUAL_REVIEW = "REQUIRES_MANUAL_REVIEW"
    REQUIRES_FRESH_PREPARATION = "REQUIRES_FRESH_PREPARATION"


class DomainWorkflowRequest(FrozenModel):
    """Reference-only request to an owning domain's high-level preparation boundary."""

    task_id: UUID
    node_id: UUID
    graph_version: int = Field(ge=1)
    domain: DomainType
    goal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_reference_ids: tuple[str, ...] = Field(default=(), max_length=32)
    task_plan_confirmation_id: UUID | None = None
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DomainPreparationResult(FrozenModel):
    """Safe handoff metadata; it deliberately contains no execute or approval capability."""

    preparation_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    node_id: UUID
    graph_version: int = Field(ge=1)
    domain: DomainType
    status: DomainPreparationStatus
    plan_ref: str | None = Field(default=None, max_length=200)
    preview_ref: str | None = Field(default=None, max_length=200)
    requires_domain_confirmation: bool
    risk_level: RiskLevel
    next_action_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{2,79}$")
    blocked_reason_codes: tuple[str, ...] = Field(default=(), max_length=20)
    execution_authorized: bool = False

    @model_validator(mode="after")
    def prohibit_execution_authority(self) -> DomainPreparationResult:
        """Prevent any workflow adapter from smuggling authority in a preparation result."""
        if self.execution_authorized:
            raise ValueError("Domain preparation cannot authorize execution")
        if self.risk_level is RiskLevel.R0 and self.requires_domain_confirmation:
            return self
        if (
            self.risk_level.severity >= RiskLevel.R1.severity
            and not self.requires_domain_confirmation
        ):
            raise ValueError("Write-capable preparation must require domain confirmation")
        return self


class DomainResultReceipt(FrozenModel):
    """Metadata-only receipt supplied by the owning domain after its own verification."""

    receipt_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    node_id: UUID
    graph_version: int = Field(ge=1)
    domain: DomainType
    status: DomainResultStatus
    verification_status: DomainVerificationStatus
    domain_transaction_ref: str | None = Field(default=None, max_length=200)
    result_ref: str | None = Field(default=None, max_length=200)
    verification_ref: str | None = Field(default=None, max_length=200)
    result_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,99}$")
    changed_count: int = Field(default=0, ge=0)
    unchanged_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    rollback_level: RollbackLevel = RollbackLevel.NONE
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def require_verification_consistency(self) -> DomainResultReceipt:
        """Forbid a verified-success label without deterministic verification evidence."""
        if self.status is DomainResultStatus.COMPLETED_VERIFIED and (
            self.verification_status is not DomainVerificationStatus.VERIFIED
            or self.verification_ref is None
        ):
            raise ValueError("Verified completion requires a deterministic verification reference")
        if self.verification_status is DomainVerificationStatus.VERIFIED and (
            self.status is DomainResultStatus.COMPLETED_UNVERIFIED
        ):
            raise ValueError("Unverified completion cannot carry verified evidence")
        return self


class DomainReconciliationRequest(FrozenModel):
    """Fresh read request for one previously dispatched domain transaction."""

    reconciliation_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    node_id: UUID
    graph_version: int = Field(ge=1)
    domain: DomainType
    domain_transaction_ref: str
    previous_result_ref: str | None = None
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DomainReconciliationResult(FrozenModel):
    """Observed current truth; it never requests automatic execution."""

    reconciliation_id: UUID
    task_id: UUID
    node_id: UUID
    domain: DomainType
    status: DomainReconciliationStatus
    evidence_ref: str | None = Field(default=None, max_length=200)
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,99}$")
    requires_user_decision: bool = True
    action_replayed: bool = False
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def prohibit_replay(self) -> DomainReconciliationResult:
        """Crash reconciliation may observe state but can never replay an action."""
        if self.action_replayed:
            raise ValueError("Domain reconciliation cannot replay an action")
        return self


class DomainRecoverySummary(FrozenModel):
    """Truthful recovery description; this is not a global rollback command."""

    domain: DomainType
    transaction_ref: str
    rollback_level: RollbackLevel
    recovery_available: bool
    recovery_action_code: str | None = Field(default=None, max_length=80)
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,99}$")
