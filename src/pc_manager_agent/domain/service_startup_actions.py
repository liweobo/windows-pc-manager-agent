"""Provider-neutral contracts for narrowly changing Windows service startup type."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, model_validator

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.service_actions import (
    ServiceObservation,
    ServiceSafetyClass,
    ServiceStableIdentity,
    ServiceStartupConfiguration,
    ServiceStartupType,
    ServiceState,
)


class ServiceStartupActionType(StrEnum):
    """The only persistent service configuration mutations registered in Stage 4C2."""

    SET_AUTOMATIC = "SET_AUTOMATIC"
    SET_MANUAL = "SET_MANUAL"
    RESTORE = "RESTORE"


class ServiceStartupManagementMode(StrEnum):
    """Whether one exact observed transition may reach a write adapter."""

    CHANGE_SUPPORTED = "CHANGE_SUPPORTED"
    RESTORE_SUPPORTED = "RESTORE_SUPPORTED"
    READ_ONLY = "READ_ONLY"
    BLOCKED = "BLOCKED"


class ServiceStartupErrorCode(StrEnum):
    """Stable privacy-safe failures for service startup-type operations."""

    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    IDENTITY_CHANGED = "IDENTITY_CHANGED"
    CONFIGURATION_CHANGED = "CONFIGURATION_CHANGED"
    RUNTIME_STATE_CHANGED = "RUNTIME_STATE_CHANGED"
    DEPENDENCY_IMPACT_BLOCKED = "DEPENDENCY_IMPACT_BLOCKED"
    UNSUPPORTED_TRANSITION = "UNSUPPORTED_TRANSITION"
    DELAYED_AUTO_UNSUPPORTED = "DELAYED_AUTO_UNSUPPORTED"
    DISABLED_TRANSITION_BLOCKED = "DISABLED_TRANSITION_BLOCKED"
    DRIVER_OR_BOOT_TYPE_BLOCKED = "DRIVER_OR_BOOT_TYPE_BLOCKED"
    PROTECTED_SERVICE_BLOCKED = "PROTECTED_SERVICE_BLOCKED"
    UNKNOWN_CONFIGURATION = "UNKNOWN_CONFIGURATION"
    PRIVILEGE_REQUIRED = "PRIVILEGE_REQUIRED"
    ELEVATED_PROCESS_BLOCKED = "ELEVATED_PROCESS_BLOCKED"
    BACKUP_UNAVAILABLE = "BACKUP_UNAVAILABLE"
    BACKUP_CORRUPT = "BACKUP_CORRUPT"
    BACKUP_NOT_VERIFIED = "BACKUP_NOT_VERIFIED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    CONFIRMATION_EXPIRED = "CONFIRMATION_EXPIRED"
    CONFIRMATION_REPLAYED = "CONFIRMATION_REPLAYED"
    AUDIT_UNAVAILABLE = "AUDIT_UNAVAILABLE"
    JOURNAL_UNAVAILABLE = "JOURNAL_UNAVAILABLE"
    ACCESS_DENIED = "ACCESS_DENIED"
    RESTORE_CONFLICT = "RESTORE_CONFLICT"
    PLATFORM_ERROR = "PLATFORM_ERROR"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    CANCELLED_BEFORE_DISPATCH = "CANCELLED_BEFORE_DISPATCH"


class ServiceStartupTransactionState(StrEnum):
    """Durable state machine that never auto-resumes an interrupted write."""

    BACKUP_CREATED = "BACKUP_CREATED"
    PREVIEWED = "PREVIEWED"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    AWAITING_RUNTIME_CONFIRMATION = "AWAITING_RUNTIME_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    VALIDATING = "VALIDATING"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class ServiceStartupPermissionEvidence(FrozenModel):
    """Least-privilege result of opening an exact service configuration handle."""

    can_query_configuration: bool
    can_change_configuration: bool
    process_elevated: bool
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def allows_change(self) -> bool:
        """Allow only a non-elevated caller with query and change-config rights."""
        return (
            self.can_query_configuration
            and self.can_change_configuration
            and not self.process_elevated
        )

    def canonical_digest(self) -> str:
        """Hash permission conclusions while excluding observation time."""
        return canonical_service_startup_digest(
            {
                "can_query_configuration": self.can_query_configuration,
                "can_change_configuration": self.can_change_configuration,
                "process_elevated": self.process_elevated,
            }
        )


class ServiceStartupImpact(FrozenModel):
    """Read-only dependency and future-start impact shown before confirmation."""

    dependency_names: tuple[str, ...]
    dependent_names: tuple[str, ...]
    current_runtime_state: ServiceState
    runtime_change_expected: bool = False
    summary: str = Field(min_length=1, max_length=1_000)

    def canonical_digest(self) -> str:
        """Bind confirmation to deterministic dependency and runtime impact evidence."""
        return canonical_service_startup_digest(self.model_dump(mode="json"))


class ServiceStartupSafetyAssessment(FrozenModel):
    """Independent policy result for one exact source-to-target transition."""

    identity_digest: str = Field(min_length=64, max_length=64)
    source_configuration_digest: str = Field(min_length=64, max_length=64)
    safety_class: ServiceSafetyClass
    management_mode: ServiceStartupManagementMode
    allowed: bool
    reason_codes: tuple[ServiceStartupErrorCode, ...]
    explanation: str = Field(min_length=1, max_length=1_000)


class ServiceStartupBackupPayload(FrozenModel):
    """Exact original configuration encrypted before Preview or mutation."""

    stable_identity: ServiceStableIdentity
    display_name: str = Field(min_length=1, max_length=256)
    original_configuration: ServiceStartupConfiguration
    original_runtime_state: ServiceState
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def canonical_digest(self) -> str:
        """Hash decrypted backup content for immediate and restore-time verification."""
        return canonical_service_startup_digest(self.model_dump(mode="json"))


class ServiceStartupBackupReference(FrozenModel):
    """Non-secret reference to one verified encrypted startup configuration backup."""

    backup_id: UUID = Field(default_factory=uuid4)
    identity_digest: str = Field(min_length=64, max_length=64)
    payload_digest: str = Field(min_length=64, max_length=64)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    verified: bool = True


class ServiceStartupActionPlan(FrozenModel):
    """Immutable R2 plan for exactly one Automatic/Manual configuration transition."""

    plan_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID = Field(default_factory=uuid4)
    operation_id: UUID = Field(default_factory=uuid4)
    plan_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_goal: str = Field(min_length=1, max_length=2_000)
    summary: str = Field(min_length=1, max_length=500)
    action: ServiceStartupActionType
    target_identity: ServiceStableIdentity
    display_name: str = Field(min_length=1, max_length=256)
    source_configuration: ServiceStartupConfiguration
    target_configuration: ServiceStartupConfiguration
    expected_runtime_state: ServiceState
    expected_state_digest: str = Field(min_length=64, max_length=64)
    expected_impact_digest: str = Field(min_length=64, max_length=64)
    expected_permission_digest: str = Field(min_length=64, max_length=64)
    backup_id: UUID
    backup_digest: str = Field(min_length=64, max_length=64)
    restore_source_backup_id: UUID | None = None
    risk_level: RiskLevel = RiskLevel.R2
    rollback_level: RollbackLevel = RollbackLevel.FULL
    requires_plan_confirmation: bool = True
    requires_runtime_confirmation: bool = True

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        """Reject broad, delayed, disabled, no-op, or weakly confirmed plans."""
        supported = {ServiceStartupType.AUTOMATIC, ServiceStartupType.MANUAL}
        if self.source_configuration.startup_type not in supported:
            raise ValueError("Source startup type is outside the Stage 4C2 write boundary")
        if self.target_configuration.startup_type not in supported:
            raise ValueError("Target startup type is outside the Stage 4C2 write boundary")
        if (
            self.source_configuration.delayed_auto_start
            or self.target_configuration.delayed_auto_start
        ):
            raise ValueError("Delayed automatic startup is read-only in Stage 4C2")
        if self.source_configuration == self.target_configuration:
            raise ValueError("Service startup configuration plan cannot be a no-op")
        if self.action is ServiceStartupActionType.SET_AUTOMATIC and (
            self.target_configuration.startup_type is not ServiceStartupType.AUTOMATIC
        ):
            raise ValueError("SET_AUTOMATIC requires a non-delayed Automatic target")
        if self.action is ServiceStartupActionType.SET_MANUAL and (
            self.target_configuration.startup_type is not ServiceStartupType.MANUAL
        ):
            raise ValueError("SET_MANUAL requires a Manual target")
        if (
            self.action is ServiceStartupActionType.RESTORE
            and self.restore_source_backup_id is None
        ):
            raise ValueError("RESTORE must bind the original change backup")
        if self.risk_level is not RiskLevel.R2:
            raise ValueError("Narrow Automatic/Manual transitions are classified R2")
        if self.rollback_level is not RollbackLevel.FULL:
            raise ValueError("A verified backup is mandatory for conditional FULL rollback")
        if not self.requires_plan_confirmation or not self.requires_runtime_confirmation:
            raise ValueError("Service startup writes require both confirmation tiers")
        return self

    def canonical_digest(self) -> str:
        """Hash every field that authorizes the exact configuration transition."""
        return canonical_service_startup_digest(self.model_dump(mode="json"))


class ServiceStartupActionPreview(FrozenModel):
    """Read-only Preview bound to identity, configuration, impact, permission, and backup."""

    preview_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID
    plan_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    action: ServiceStartupActionType
    observation: ServiceObservation
    target_configuration: ServiceStartupConfiguration
    safety: ServiceStartupSafetyAssessment
    impact: ServiceStartupImpact
    permissions: ServiceStartupPermissionEvidence
    current_state_digest: str = Field(min_length=64, max_length=64)
    backup_id: UUID
    backup_digest: str = Field(min_length=64, max_length=64)
    backup_verified: bool
    risk_level: RiskLevel = RiskLevel.R2
    rollback_level: RollbackLevel = RollbackLevel.FULL

    @property
    def executable(self) -> bool:
        """Return true only when all deterministic gates still authorize the write."""
        return (
            self.safety.allowed
            and self.permissions.allows_change
            and self.backup_verified
            and not self.observation.state.is_pending
            and self.current_state_digest == self.observation.state_digest()
            and not self.impact.runtime_change_expected
        )

    def canonical_digest(self) -> str:
        """Hash the full Preview for one-time confirmation binding."""
        return canonical_service_startup_digest(self.model_dump(mode="json"))


class ServiceStartupActionRequest(FrozenModel):
    """Strict narrow-tool input containing only one exact startup-type transition."""

    action: ServiceStartupActionType
    identity: ServiceStableIdentity
    expected_source_configuration: ServiceStartupConfiguration
    target_configuration: ServiceStartupConfiguration
    expected_runtime_state: ServiceState
    expected_impact_digest: str = Field(min_length=64, max_length=64)
    backup_id: UUID
    backup_digest: str = Field(min_length=64, max_length=64)


class ServiceStartupMutationResult(FrozenModel):
    """Verified persistent configuration result with explicit runtime-state evidence."""

    action: ServiceStartupActionType
    identity_digest: str = Field(min_length=64, max_length=64)
    before_configuration: ServiceStartupConfiguration
    after_configuration: ServiceStartupConfiguration
    before_runtime_state: ServiceState
    after_runtime_state: ServiceState
    change_dispatched: bool
    verified: bool
    runtime_unchanged: bool
    message: str = Field(min_length=1, max_length=1_000)
    started_at: datetime
    completed_at: datetime


class ServiceStartupActionTransaction(FrozenModel):
    """Public durable transaction view without encrypted backup contents."""

    transaction_id: UUID
    operation_id: UUID
    plan_id: UUID
    preview_id: UUID
    action: ServiceStartupActionType
    service_name: str
    identity_digest: str = Field(min_length=64, max_length=64)
    source_configuration_digest: str = Field(min_length=64, max_length=64)
    target_configuration_digest: str = Field(min_length=64, max_length=64)
    state: ServiceStartupTransactionState
    plan_digest: str = Field(min_length=64, max_length=64)
    preview_digest: str = Field(min_length=64, max_length=64)
    backup_id: UUID
    backup_digest: str = Field(min_length=64, max_length=64)
    plan_confirmation_id: UUID | None = None
    runtime_confirmation_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    error_code: ServiceStartupErrorCode | None = None
    error_message: str | None = None
    result: dict[str, JsonValue] | None = None


class ServiceStartupChangeRecord(FrozenModel):
    """Successful Agent-owned change that remains eligible for conflict-checked restore."""

    original_transaction_id: UUID
    backup_id: UUID
    backup_digest: str = Field(min_length=64, max_length=64)
    stable_identity: ServiceStableIdentity
    display_name: str = Field(min_length=1, max_length=256)
    original_configuration: ServiceStartupConfiguration
    written_configuration: ServiceStartupConfiguration
    original_runtime_state: ServiceState
    changed_at: datetime
    restored_at: datetime | None = None


def canonical_service_startup_digest(payload: object) -> str:
    """Return a deterministic SHA-256 digest for Stage 4C2 confirmation bindings."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
