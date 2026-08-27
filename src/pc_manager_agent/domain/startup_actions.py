"""Provider-neutral models for controlled current-user startup management."""

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


class StartupSource(StrEnum):
    """Finite startup sources that Stage 4B can inspect."""

    HKCU_RUN = "HKCU_RUN"
    HKCU_RUN_ONCE = "HKCU_RUN_ONCE"
    HKLM_RUN = "HKLM_RUN"
    HKLM_RUN_ONCE = "HKLM_RUN_ONCE"
    USER_STARTUP_FOLDER = "USER_STARTUP_FOLDER"
    COMMON_STARTUP_FOLDER = "COMMON_STARTUP_FOLDER"


class StartupActionType(StrEnum):
    """The only startup mutations registered in Stage 4B."""

    DISABLE = "DISABLE"
    RESTORE = "RESTORE"


class StartupEntryStatus(StrEnum):
    """Conservative configuration state, not a future launch guarantee."""

    ENABLED = "ENABLED"
    DISABLED_BY_WINDOWS = "DISABLED_BY_WINDOWS"
    AGENT_DISABLED = "AGENT_DISABLED"
    UNKNOWN = "UNKNOWN"


class StartupSafetyClass(StrEnum):
    """Deterministic safety classifications for startup entries."""

    USER_THIRD_PARTY = "USER_THIRD_PARTY"
    USER_MICROSOFT = "USER_MICROSOFT"
    SYSTEM_COMPONENT = "SYSTEM_COMPONENT"
    SECURITY_SOFTWARE = "SECURITY_SOFTWARE"
    DRIVER_RELATED = "DRIVER_RELATED"
    AGENT_COMPONENT = "AGENT_COMPONENT"
    ENTERPRISE_MANAGED = "ENTERPRISE_MANAGED"
    UNKNOWN = "UNKNOWN"


class StartupManagementMode(StrEnum):
    """Whether the exact entry may be changed by the narrow Stage 4B tools."""

    DISABLE_SUPPORTED = "DISABLE_SUPPORTED"
    RESTORE_SUPPORTED = "RESTORE_SUPPORTED"
    READ_ONLY = "READ_ONLY"
    BLOCKED = "BLOCKED"


class StartupSafetyDecision(StrEnum):
    """Final deterministic decision that model output cannot override."""

    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


class StartupErrorCode(StrEnum):
    """Stable error identifiers for UI messages and privacy-minimized audit."""

    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    IDENTITY_CHANGED = "IDENTITY_CHANGED"
    UNSUPPORTED_SOURCE = "UNSUPPORTED_SOURCE"
    UNSUPPORTED_VALUE_TYPE = "UNSUPPORTED_VALUE_TYPE"
    UNSUPPORTED_SHORTCUT = "UNSUPPORTED_SHORTCUT"
    UNKNOWN_EXECUTABLE = "UNKNOWN_EXECUTABLE"
    UNKNOWN_PUBLISHER = "UNKNOWN_PUBLISHER"
    BLOCKED_SYSTEM_COMPONENT = "BLOCKED_SYSTEM_COMPONENT"
    BLOCKED_SECURITY_SOFTWARE = "BLOCKED_SECURITY_SOFTWARE"
    BLOCKED_DRIVER_RELATED = "BLOCKED_DRIVER_RELATED"
    BLOCKED_AGENT_COMPONENT = "BLOCKED_AGENT_COMPONENT"
    BLOCKED_ENTERPRISE_MANAGED = "BLOCKED_ENTERPRISE_MANAGED"
    ALREADY_DISABLED = "ALREADY_DISABLED"
    ALREADY_ENABLED = "ALREADY_ENABLED"
    BACKUP_UNAVAILABLE = "BACKUP_UNAVAILABLE"
    BACKUP_CORRUPT = "BACKUP_CORRUPT"
    BACKUP_NOT_VERIFIED = "BACKUP_NOT_VERIFIED"
    DESTINATION_CONFLICT = "DESTINATION_CONFLICT"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    CONFIRMATION_EXPIRED = "CONFIRMATION_EXPIRED"
    CONFIRMATION_REPLAYED = "CONFIRMATION_REPLAYED"
    AUDIT_UNAVAILABLE = "AUDIT_UNAVAILABLE"
    ACCESS_DENIED = "ACCESS_DENIED"
    PLATFORM_ERROR = "PLATFORM_ERROR"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class StartupTransactionState(StrEnum):
    """Durable lifecycle for one startup mutation."""

    PLANNED = "PLANNED"
    PREVIEWED = "PREVIEWED"
    BACKUP_CREATED = "BACKUP_CREATED"
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
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"


class RegistryStartupIdentity(FrozenModel):
    """Stable evidence for one named registry value and approval record."""

    hive: str = Field(pattern=r"^HK(?:CU|LM)$")
    key_path: str = Field(min_length=1, max_length=1_000)
    value_name: str = Field(min_length=1, max_length=16_383)
    value_type: int = Field(ge=0)
    value_data_digest: str = Field(min_length=64, max_length=64)
    command_fingerprint: str = Field(min_length=64, max_length=64)
    resolved_executable_path: Path | None = None
    approval_data_digest: str | None = Field(default=None, min_length=64, max_length=64)
    registry_view: str = Field(default="NATIVE", pattern=r"^(?:NATIVE|32|64)$")

    def canonical_digest(self) -> str:
        """Hash source identity fields used for execution-time revalidation."""
        return _canonical_digest(self.model_dump(mode="json"))


class FolderStartupIdentity(FrozenModel):
    """Stable evidence for one current-user Startup Folder shell link."""

    shortcut_path: Path
    volume_serial: int = Field(ge=0)
    file_id: str = Field(min_length=1, max_length=64)
    shortcut_digest: str = Field(min_length=64, max_length=64)
    resolved_target: Path
    arguments_fingerprint: str = Field(min_length=64, max_length=64)
    working_directory_fingerprint: str = Field(min_length=64, max_length=64)
    approval_data_digest: str | None = Field(default=None, min_length=64, max_length=64)

    def canonical_digest(self) -> str:
        """Hash shortcut identity, content, and execution-relevant resolution."""
        payload = self.model_dump(mode="json")
        payload["shortcut_path"] = os.path.normcase(str(self.shortcut_path))
        payload["resolved_target"] = os.path.normcase(str(self.resolved_target))
        return _canonical_digest(payload)


class StartupIdentity(FrozenModel):
    """Exactly one source-specific startup identity."""

    source: StartupSource
    registry: RegistryStartupIdentity | None = None
    folder: FolderStartupIdentity | None = None

    @model_validator(mode="after")
    def require_matching_identity(self) -> Self:
        """Reject source/identity mixtures before they reach a platform adapter."""
        registry_sources = {
            StartupSource.HKCU_RUN,
            StartupSource.HKCU_RUN_ONCE,
            StartupSource.HKLM_RUN,
            StartupSource.HKLM_RUN_ONCE,
        }
        if self.source in registry_sources:
            if self.registry is None or self.folder is not None:
                raise ValueError("Registry startup sources require only registry identity")
        elif self.folder is None or self.registry is not None:
            raise ValueError("Startup Folder sources require only folder identity")
        return self

    def canonical_digest(self) -> str:
        """Return one source-prefixed stable identity digest."""
        detail = self.registry or self.folder
        if detail is None:  # pragma: no cover - model validator makes this unreachable
            raise ValueError("Startup identity detail is missing")
        return hashlib.sha256(
            f"{self.source.value}\n{detail.canonical_digest()}".encode()
        ).hexdigest()


class StartupObservation(FrozenModel):
    """Privacy-minimized live metadata used by Preview and target selection."""

    identity: StartupIdentity
    display_name: str = Field(min_length=1, max_length=500)
    publisher: str | None = Field(default=None, max_length=500)
    executable_path: Path | None = None
    command_summary: str = Field(min_length=1, max_length=500)
    scope: str = Field(pattern=r"^(?:CURRENT_USER|ALL_USERS)$")
    status: StartupEntryStatus
    status_evidence: str = Field(min_length=1, max_length=500)
    management_mode: StartupManagementMode = StartupManagementMode.READ_ONLY

    def current_state_digest(self) -> str:
        """Bind Preview to identity plus the visible configuration state."""
        return _canonical_digest(
            {
                "identity": self.identity.canonical_digest(),
                "status": self.status.value,
                "status_evidence": self.status_evidence,
                "management_mode": self.management_mode.value,
                "publisher": self.publisher,
                "executable_path": (
                    os.path.normcase(str(self.executable_path))
                    if self.executable_path is not None
                    else None
                ),
            }
        )


class StartupSafetyAssessment(FrozenModel):
    """Deterministic classification and allow/block result for one live entry."""

    identity_digest: str = Field(min_length=64, max_length=64)
    safety_class: StartupSafetyClass
    decision: StartupSafetyDecision
    management_mode: StartupManagementMode
    reason_codes: tuple[StartupErrorCode, ...]
    explanation: str = Field(min_length=1, max_length=1_000)


class StartupBackupPayload(FrozenModel):
    """Exact restore material; only an encrypted vault may persist this model."""

    original_identity: StartupIdentity
    source: StartupSource
    registry_value_data_b64: str | None = Field(default=None, repr=False)
    registry_value_type: int | None = Field(default=None, ge=0)
    approval_data_b64: str | None = Field(default=None, repr=False)
    shortcut_data_b64: str | None = Field(default=None, repr=False)
    disabled_storage_path: Path | None = None
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def require_source_material(self) -> Self:
        """Require exact registry or shortcut bytes, never both."""
        if self.source in {
            StartupSource.HKCU_RUN,
            StartupSource.HKCU_RUN_ONCE,
            StartupSource.HKLM_RUN,
        }:
            if (
                self.registry_value_data_b64 is None
                or self.registry_value_type is None
                or self.shortcut_data_b64 is not None
            ):
                raise ValueError("Registry backup requires exact value bytes and type")
            registry = self.original_identity.registry
            if self.source is StartupSource.HKLM_RUN and (
                registry is None
                or registry.hive != "HKLM"
                or registry.key_path.casefold()
                != r"Software\Microsoft\Windows\CurrentVersion\Run".casefold()
                or registry.registry_view not in {"32", "64"}
            ):
                raise ValueError("Machine backup requires an explicit 32/64-bit HKLM Run value")
        elif self.source is StartupSource.USER_STARTUP_FOLDER:
            if self.shortcut_data_b64 is None or self.disabled_storage_path is None:
                raise ValueError("Startup Folder backup requires exact link bytes and storage path")
            if self.registry_value_data_b64 is not None:
                raise ValueError("Folder backup cannot contain registry value bytes")
        else:
            raise ValueError("Read-only startup sources cannot create write backups")
        return self

    def canonical_digest(self) -> str:
        """Hash decrypted exact backup material for corruption detection."""
        return _canonical_digest(self.model_dump(mode="json"))


class StartupBackupReference(FrozenModel):
    """Non-secret durable reference to one verified encrypted backup."""

    backup_id: UUID = Field(default_factory=uuid4)
    identity_digest: str = Field(min_length=64, max_length=64)
    payload_digest: str = Field(min_length=64, max_length=64)
    source: StartupSource
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    verified: bool = True


class StartupActionPlan(FrozenModel):
    """Immutable single-object startup action plan."""

    plan_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID = Field(default_factory=uuid4)
    operation_id: UUID = Field(default_factory=uuid4)
    plan_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_goal: str = Field(min_length=1, max_length=2_000)
    summary: str = Field(min_length=1, max_length=500)
    action: StartupActionType
    target_identity: StartupIdentity
    target_name: str = Field(min_length=1, max_length=500)
    expected_state_digest: str = Field(min_length=64, max_length=64)
    backup_id: UUID
    backup_digest: str = Field(min_length=64, max_length=64)
    risk_level: RiskLevel = RiskLevel.R2
    rollback_level: RollbackLevel = RollbackLevel.FULL
    requires_plan_confirmation: bool = True
    requires_runtime_confirmation: bool = True

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        """Enforce the conservative two-confirmation FULL-backup contract."""
        if self.risk_level is not RiskLevel.R2:
            raise ValueError("Stage 4B startup mutations are conservatively classified R2")
        if self.rollback_level is not RollbackLevel.FULL:
            raise ValueError("Startup writes require an exact verified FULL backup")
        if not self.requires_plan_confirmation or not self.requires_runtime_confirmation:
            raise ValueError("Startup writes require plan and immediate confirmation")
        return self

    def canonical_digest(self) -> str:
        """Hash every execution-relevant plan field."""
        return _canonical_digest(self.model_dump(mode="json"))


class StartupActionPreview(FrozenModel):
    """Read-only live Preview bound to exact identity, state, and backup."""

    preview_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID
    plan_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    action: StartupActionType
    observation: StartupObservation
    assessment: StartupSafetyAssessment
    current_state_digest: str = Field(min_length=64, max_length=64)
    backup_id: UUID
    backup_digest: str = Field(min_length=64, max_length=64)
    backup_verified: bool
    risk_level: RiskLevel = RiskLevel.R2
    rollback_level: RollbackLevel = RollbackLevel.FULL

    @property
    def executable(self) -> bool:
        """Return true only for an allowed exact entry with verified restore data."""
        return (
            self.assessment.decision is StartupSafetyDecision.ALLOW
            and self.backup_verified
            and self.current_state_digest == self.observation.current_state_digest()
        )

    def canonical_digest(self) -> str:
        """Hash the complete live Preview for confirmation binding."""
        return _canonical_digest(self.model_dump(mode="json"))


class StartupActionRequest(FrozenModel):
    """Strict registered-tool input containing references, never exact command bytes."""

    action: StartupActionType
    identity: StartupIdentity
    expected_state_digest: str = Field(min_length=64, max_length=64)
    backup_id: UUID
    backup_digest: str = Field(min_length=64, max_length=64)


class StartupMutationResult(FrozenModel):
    """Verified configuration result; it makes no future-launch claim."""

    action: StartupActionType
    identity_digest: str = Field(min_length=64, max_length=64)
    before_state_digest: str = Field(min_length=64, max_length=64)
    after_status: StartupEntryStatus
    verified: bool
    rollback_performed: bool = False
    rollback_verified: bool = False
    message: str = Field(min_length=1, max_length=1_000)
    started_at: datetime
    completed_at: datetime


class StartupActionTransaction(FrozenModel):
    """Public durable transaction view without encrypted backup contents."""

    transaction_id: UUID
    operation_id: UUID
    plan_id: UUID
    preview_id: UUID
    action: StartupActionType
    target_name: str
    identity_digest: str = Field(min_length=64, max_length=64)
    state: StartupTransactionState
    plan_digest: str = Field(min_length=64, max_length=64)
    preview_digest: str = Field(min_length=64, max_length=64)
    backup_id: UUID
    backup_digest: str = Field(min_length=64, max_length=64)
    plan_confirmation_id: UUID | None = None
    runtime_confirmation_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    error_code: StartupErrorCode | None = None
    error_message: str | None = None
    result: dict[str, JsonValue] | None = None


class DisabledStartupRecord(FrozenModel):
    """One Agent-disabled entry whose verified backup remains restorable."""

    original_transaction_id: UUID
    backup_id: UUID
    backup_digest: str = Field(min_length=64, max_length=64)
    identity: StartupIdentity
    display_name: str
    original_observation: StartupObservation
    disabled_at: datetime
    restored_at: datetime | None = None


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
