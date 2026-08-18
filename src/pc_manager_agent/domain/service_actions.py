"""Provider-neutral models for controlled Windows service state changes."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, model_validator

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel


class ServiceActionType(StrEnum):
    """Finite service actions available in Stage 4C1."""

    START = "START"
    STOP = "STOP"
    RESTART = "RESTART"


class ServiceStepType(StrEnum):
    """Narrow SCM mutations used to compose an action."""

    START = "START"
    STOP = "STOP"


class ServiceState(StrEnum):
    """SCM states relevant to planning and verification."""

    STOPPED = "STOPPED"
    START_PENDING = "START_PENDING"
    STOP_PENDING = "STOP_PENDING"
    RUNNING = "RUNNING"
    CONTINUE_PENDING = "CONTINUE_PENDING"
    PAUSE_PENDING = "PAUSE_PENDING"
    PAUSED = "PAUSED"
    UNKNOWN = "UNKNOWN"

    @property
    def is_pending(self) -> bool:
        """Return whether conflicting control requests must not be sent."""
        return self in {
            ServiceState.START_PENDING,
            ServiceState.STOP_PENDING,
            ServiceState.CONTINUE_PENDING,
            ServiceState.PAUSE_PENDING,
        }


class ServiceSafetyClass(StrEnum):
    """Defense-in-depth classifications for service control decisions."""

    USER_THIRD_PARTY_SERVICE = "USER_THIRD_PARTY_SERVICE"
    THIRD_PARTY_SYSTEM_SERVICE = "THIRD_PARTY_SYSTEM_SERVICE"
    WINDOWS_CORE_SERVICE = "WINDOWS_CORE_SERVICE"
    SECURITY_SERVICE = "SECURITY_SERVICE"
    DRIVER_SERVICE = "DRIVER_SERVICE"
    NETWORK_CRITICAL_SERVICE = "NETWORK_CRITICAL_SERVICE"
    LOGIN_CRITICAL_SERVICE = "LOGIN_CRITICAL_SERVICE"
    STORAGE_CRITICAL_SERVICE = "STORAGE_CRITICAL_SERVICE"
    UPDATE_SERVICE = "UPDATE_SERVICE"
    AGENT_SERVICE = "AGENT_SERVICE"
    ENTERPRISE_MANAGED = "ENTERPRISE_MANAGED"
    UNKNOWN = "UNKNOWN"


class ServiceSafetyDecision(StrEnum):
    """Final deterministic allow/block decision."""

    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


class ServiceErrorCode(StrEnum):
    """Stable privacy-safe errors for UI and audit."""

    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    TARGET_QUERY_INVALID = "TARGET_QUERY_INVALID"
    SERVICE_CONFIGURATION_CHANGED = "SERVICE_CONFIGURATION_CHANGED"
    SERVICE_STATE_CHANGED = "SERVICE_STATE_CHANGED"
    DEPENDENCY_GRAPH_CHANGED = "DEPENDENCY_GRAPH_CHANGED"
    DEPENDENCY_NOT_RUNNING = "DEPENDENCY_NOT_RUNNING"
    DEPENDENT_SERVICE_RUNNING = "DEPENDENT_SERVICE_RUNNING"
    PENDING_STATE = "PENDING_STATE"
    PRIVILEGE_REQUIRED = "PRIVILEGE_REQUIRED"
    ELEVATED_PROCESS_BLOCKED = "ELEVATED_PROCESS_BLOCKED"
    ACTION_NOT_SUPPORTED = "ACTION_NOT_SUPPORTED"
    BLOCKED_PROTECTED_SERVICE = "BLOCKED_PROTECTED_SERVICE"
    BLOCKED_DRIVER_SERVICE = "BLOCKED_DRIVER_SERVICE"
    BLOCKED_AGENT_SERVICE = "BLOCKED_AGENT_SERVICE"
    BLOCKED_UNKNOWN_SERVICE = "BLOCKED_UNKNOWN_SERVICE"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    CONFIRMATION_EXPIRED = "CONFIRMATION_EXPIRED"
    CONFIRMATION_REPLAYED = "CONFIRMATION_REPLAYED"
    AUDIT_UNAVAILABLE = "AUDIT_UNAVAILABLE"
    PLATFORM_ERROR = "PLATFORM_ERROR"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED_BEFORE_DISPATCH = "CANCELLED_BEFORE_DISPATCH"
    CANCELLED_AFTER_STOP = "CANCELLED_AFTER_STOP"


class ServiceTransactionState(StrEnum):
    """Durable state machine for one service action."""

    PREVIEWED = "PREVIEWED"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    AWAITING_RUNTIME_CONFIRMATION = "AWAITING_RUNTIME_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    VALIDATING = "VALIDATING"
    EXECUTING_STOP = "EXECUTING_STOP"
    WAITING_STOPPED = "WAITING_STOPPED"
    STOP_COMPLETED = "STOP_COMPLETED"
    EXECUTING_START = "EXECUTING_START"
    WAITING_RUNNING = "WAITING_RUNNING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class ServiceStartupType(StrEnum):
    """Normalized SCM startup types used by read and write safety checks."""

    AUTOMATIC = "AUTOMATIC"
    AUTOMATIC_DELAYED = "AUTOMATIC_DELAYED"
    MANUAL = "MANUAL"
    DISABLED = "DISABLED"
    BOOT = "BOOT"
    SYSTEM = "SYSTEM"
    UNKNOWN = "UNKNOWN"


class ServiceStartupConfiguration(FrozenModel):
    """Mutable startup configuration kept separate from stable service identity."""

    startup_type: ServiceStartupType
    delayed_auto_start: bool

    def canonical_digest(self) -> str:
        """Hash exactly the startup fields that Stage 4C2 can observe."""
        return _digest(self.model_dump(mode="json"))


class ServiceStableIdentity(FrozenModel):
    """Stable service evidence revalidated before every SCM operation."""

    service_name: str = Field(min_length=1, max_length=256)
    service_type: int = Field(ge=0)
    binary_path_fingerprint: str = Field(min_length=64, max_length=64)
    service_account: str = Field(min_length=1, max_length=512)

    def canonical_digest(self) -> str:
        """Hash stable fields while excluding display, startup, and runtime state."""
        return _digest(
            {
                "service_name": self.service_name.casefold(),
                "service_type": self.service_type,
                "binary_path_fingerprint": self.binary_path_fingerprint,
                "service_account": self.service_account.casefold(),
            }
        )


# Compatibility alias retained for Stage 4C1 public imports. The model itself now
# intentionally represents only stable identity fields.
ServiceIdentity = ServiceStableIdentity


class ServiceRelation(FrozenModel):
    """One exact dependency or dependent with live state."""

    service_name: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=256)
    state: ServiceState


class ServicePermissionEvidence(FrozenModel):
    """Action-specific handle-open evidence gathered without mutation."""

    can_query: bool
    can_start: bool
    can_stop: bool
    can_enumerate_dependents: bool
    process_elevated: bool
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def allows(self, action: ServiceActionType) -> bool:
        """Return whether least-privilege handles are available for the action."""
        if self.process_elevated or not self.can_query:
            return False
        if action is ServiceActionType.START:
            return self.can_start
        if action is ServiceActionType.STOP:
            return self.can_stop and self.can_enumerate_dependents
        return self.can_start and self.can_stop and self.can_enumerate_dependents

    def canonical_digest(self) -> str:
        """Hash stable permission conclusions, excluding check time."""
        return _digest(
            {
                "can_query": self.can_query,
                "can_start": self.can_start,
                "can_stop": self.can_stop,
                "can_enumerate_dependents": self.can_enumerate_dependents,
                "process_elevated": self.process_elevated,
            }
        )


class ServiceObservation(FrozenModel):
    """Fresh local service configuration, state, and relationship evidence."""

    identity: ServiceStableIdentity
    display_name: str = Field(min_length=1, max_length=256)
    startup_configuration: ServiceStartupConfiguration
    state: ServiceState
    controls_accepted: int = Field(ge=0)
    process_id: int = Field(ge=0)
    binary_path: Path | None = None
    publisher: str | None = Field(default=None, max_length=500)
    publisher_verified: bool = False
    description: str | None = Field(default=None, max_length=4_000)
    dependencies: tuple[ServiceRelation, ...] = ()
    dependents: tuple[ServiceRelation, ...] = ()

    def state_digest(self) -> str:
        """Bind a Preview to stable identity, startup configuration, and live state."""
        return _digest(
            {
                "identity": self.identity.canonical_digest(),
                "startup_configuration": self.startup_configuration.canonical_digest(),
                "state": self.state.value,
                "controls_accepted": self.controls_accepted,
            }
        )

    def configuration_digest(self) -> str:
        """Return the independent startup-configuration digest for TOCTOU checks."""
        return self.startup_configuration.canonical_digest()

    def dependency_digest(self) -> str:
        """Hash dependency names and states in deterministic order."""
        return _digest(
            {
                "dependencies": [
                    item.model_dump(mode="json")
                    for item in sorted(
                        self.dependencies, key=lambda value: value.service_name.casefold()
                    )
                ],
                "dependents": [
                    item.model_dump(mode="json")
                    for item in sorted(
                        self.dependents, key=lambda value: value.service_name.casefold()
                    )
                ],
            }
        )


class ServiceDependencyAssessment(FrozenModel):
    """Action-specific dependency decision; Stage 4C1 never cascades controls."""

    allowed: bool
    blocking_dependencies: tuple[ServiceRelation, ...] = ()
    blocking_dependents: tuple[ServiceRelation, ...] = ()
    graph_digest: str = Field(min_length=64, max_length=64)
    explanation: str = Field(min_length=1, max_length=1_000)


class ServiceSafetyAssessment(FrozenModel):
    """Deterministic classification and policy result for an exact service."""

    identity_digest: str = Field(min_length=64, max_length=64)
    safety_class: ServiceSafetyClass
    decision: ServiceSafetyDecision
    reason_codes: tuple[ServiceErrorCode, ...]
    explanation: str = Field(min_length=1, max_length=1_000)


class ServiceActionPlan(FrozenModel):
    """Immutable single-service plan, with restart represented as two steps."""

    plan_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID = Field(default_factory=uuid4)
    operation_id: UUID = Field(default_factory=uuid4)
    plan_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_goal: str = Field(min_length=1, max_length=2_000)
    summary: str = Field(min_length=1, max_length=500)
    target_query: str = Field(min_length=1, max_length=500)
    action: ServiceActionType
    target_identity: ServiceStableIdentity
    target_startup_configuration: ServiceStartupConfiguration
    expected_state_digest: str = Field(min_length=64, max_length=64)
    expected_dependency_digest: str = Field(min_length=64, max_length=64)
    expected_permission_digest: str = Field(min_length=64, max_length=64)
    steps: tuple[ServiceStepType, ...]
    risk_level: RiskLevel
    rollback_level: RollbackLevel = RollbackLevel.MANUAL
    requires_plan_confirmation: bool = True
    requires_runtime_confirmation: bool = True

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        """Enforce exact risk, steps, two confirmations, and truthful rollback."""
        expected_steps = {
            ServiceActionType.START: (ServiceStepType.START,),
            ServiceActionType.STOP: (ServiceStepType.STOP,),
            ServiceActionType.RESTART: (ServiceStepType.STOP, ServiceStepType.START),
        }[self.action]
        expected_risk = (
            RiskLevel.R2_HIGH_IMPACT if self.action is ServiceActionType.RESTART else RiskLevel.R2
        )
        if self.steps != expected_steps:
            raise ValueError("Service action steps do not match the requested action")
        if self.risk_level is not expected_risk:
            raise ValueError("Service action risk classification is incorrect")
        if self.rollback_level is not RollbackLevel.MANUAL:
            raise ValueError("Service state changes have MANUAL rollback capability")
        if not self.requires_plan_confirmation or not self.requires_runtime_confirmation:
            raise ValueError("Service state changes require both confirmation tiers")
        return self

    def canonical_digest(self) -> str:
        """Hash every execution-relevant plan field."""
        return _digest(self.model_dump(mode="json"))


class ServiceActionPreview(FrozenModel):
    """Read-only Preview bound to identity, state, relationships, and permissions."""

    preview_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID
    plan_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    action: ServiceActionType
    observation: ServiceObservation
    safety: ServiceSafetyAssessment
    dependencies: ServiceDependencyAssessment
    permissions: ServicePermissionEvidence
    current_state_digest: str = Field(min_length=64, max_length=64)
    risk_level: RiskLevel
    rollback_level: RollbackLevel = RollbackLevel.MANUAL

    @property
    def executable(self) -> bool:
        """Return true only when every deterministic gate allows the exact action."""
        return (
            self.safety.decision is ServiceSafetyDecision.ALLOW
            and self.dependencies.allowed
            and self.permissions.allows(self.action)
            and not self.observation.state.is_pending
            and self.current_state_digest == self.observation.state_digest()
        )

    def canonical_digest(self) -> str:
        """Hash the complete Preview for confirmation binding."""
        return _digest(self.model_dump(mode="json"))


class ServiceStepRequest(FrozenModel):
    """Strict registered-tool input for one exact SCM transition."""

    transaction_id: UUID
    step: ServiceStepType
    identity: ServiceStableIdentity
    expected_identity_digest: str = Field(min_length=64, max_length=64)
    expected_startup_configuration_digest: str = Field(min_length=64, max_length=64)
    expected_state: ServiceState
    timeout_seconds: float = Field(gt=0, le=120)


class ServiceStepResult(FrozenModel):
    """Verified result of one narrow start or stop request."""

    step: ServiceStepType
    identity_digest: str = Field(min_length=64, max_length=64)
    before_state: ServiceState
    after_state: ServiceState
    control_dispatched: bool
    verified: bool
    cancellation_requested_after_dispatch: bool = False
    message: str = Field(min_length=1, max_length=1_000)
    started_at: datetime
    completed_at: datetime


class ServiceActionResult(FrozenModel):
    """Combined action result preserving explicit restart substeps."""

    action: ServiceActionType
    transaction_id: UUID
    steps: tuple[ServiceStepResult, ...]
    final_state: ServiceState
    completed: bool
    partially_completed: bool = False
    no_op: bool = False
    message: str = Field(min_length=1, max_length=2_000)
    started_at: datetime
    completed_at: datetime


class ServiceActionTransaction(FrozenModel):
    """Public durable service transaction without sensitive service contents."""

    transaction_id: UUID
    operation_id: UUID
    plan_id: UUID
    preview_id: UUID
    action: ServiceActionType
    service_name: str
    identity_digest: str = Field(min_length=64, max_length=64)
    state: ServiceTransactionState
    plan_digest: str = Field(min_length=64, max_length=64)
    preview_digest: str = Field(min_length=64, max_length=64)
    next_step_index: int = Field(ge=0)
    step_in_progress: bool
    plan_confirmation_id: UUID | None = None
    runtime_confirmation_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    error_code: ServiceErrorCode | None = None
    error_message: str | None = None
    result: dict[str, JsonValue] | None = None


class ServiceInventoryItem(FrozenModel):
    """Read-only Services-page row with action-specific eligibility."""

    observation: ServiceObservation
    safety_class: ServiceSafetyClass
    start_allowed: bool
    stop_allowed: bool
    restart_allowed: bool
    explanation: str = Field(min_length=1, max_length=1_000)


def canonical_binary_fingerprint(raw_binary_path: str) -> str:
    """Hash the exact SCM binary-path configuration without executing it."""
    return hashlib.sha256(raw_binary_path.encode("utf-8", errors="surrogatepass")).hexdigest()


def canonical_path(path: Path) -> Path:
    """Normalize a local comparison path without requiring it to exist."""
    return Path(os.path.normcase(str(path.resolve(strict=False))))


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
