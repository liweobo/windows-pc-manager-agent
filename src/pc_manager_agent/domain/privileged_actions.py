"""Strict provider-neutral contracts shared by Stage 4X1 through Stage 4X3."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.service_actions import (
    ServiceStableIdentity,
    ServiceStartupConfiguration,
    ServiceStartupType,
    ServiceState,
)
from pc_manager_agent.domain.software_uninstall_execution import MsiInstallContext
from pc_manager_agent.domain.startup_actions import (
    RegistryStartupIdentity,
    StartupIdentity,
    StartupSource,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareArchitecture, SoftwareScope

PROTOCOL_VERSION: Literal[2] = 2
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PolicyVersion = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")]

SERVICE_CONTROL_SCHEMA_VERSION: Literal[1] = 1
SERVICE_CONTROL_POLICY_VERSION: Literal["stage4x2-service-control-v1"] = (
    "stage4x2-service-control-v1"
)
SERVICE_STARTUP_SCHEMA_VERSION: Literal[1] = 1
SERVICE_STARTUP_POLICY_VERSION: Literal["stage4x3-service-startup-v1"] = (
    "stage4x3-service-startup-v1"
)
MACHINE_STARTUP_SCHEMA_VERSION: Literal[1] = 1
MACHINE_STARTUP_POLICY_VERSION: Literal["stage4x3-machine-startup-v1"] = (
    "stage4x3-machine-startup-v1"
)
MACHINE_MSI_SCHEMA_VERSION: Literal[1] = 1
MACHINE_MSI_POLICY_VERSION: Literal["stage4x3-machine-msi-v1"] = "stage4x3-machine-msi-v1"


class PrivilegedBrokerMode(StrEnum):
    """Explicit privileged runtime modes; the default remains disabled."""

    DISABLED = "disabled"
    MOCK = "mock"
    WINDOWS = "windows"


class PrivilegedExecutionMode(StrEnum):
    """Whether a Preview authorizes synthetic validation or the Windows Broker."""

    MOCK = "MOCK"
    WINDOWS_ELEVATED = "WINDOWS_ELEVATED"


class PrivilegedActionType(StrEnum):
    """Finite privileged actions understood by protocol version 2."""

    SERVICE_START = "SERVICE_START"
    SERVICE_STOP = "SERVICE_STOP"
    SERVICE_RESTART = "SERVICE_RESTART"
    SERVICE_STARTUP_TYPE_CHANGE = "SERVICE_STARTUP_TYPE_CHANGE"
    SERVICE_STARTUP_TYPE_RESTORE = "SERVICE_STARTUP_TYPE_RESTORE"
    STARTUP_MACHINE_DISABLE = "STARTUP_MACHINE_DISABLE"
    STARTUP_MACHINE_RESTORE = "STARTUP_MACHINE_RESTORE"
    MSI_UNINSTALL_MACHINE = "MSI_UNINSTALL_MACHINE"


class PrivilegeResolutionStatus(StrEnum):
    """Deterministic routing decision made after the safety policy."""

    NOT_REQUIRED = "NOT_REQUIRED"
    REQUIRED = "REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class PrivilegeRequirement(StrEnum):
    """Windows privilege level believed necessary for one exact action."""

    STANDARD_USER = "STANDARD_USER"
    ELEVATED_ADMIN_REQUIRED = "ELEVATED_ADMIN_REQUIRED"
    SYSTEM_REQUIRED = "SYSTEM_REQUIRED"
    TRUSTED_INSTALLER_OR_UNSUPPORTED = "TRUSTED_INSTALLER_OR_UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


class PrivilegeResolution(FrozenModel):
    """Permission routing evidence that cannot override a safety block."""

    status: PrivilegeResolutionStatus
    requirement: PrivilegeRequirement
    safety_allowed: bool
    preflight_complete: bool
    access_failure_code: int | None = Field(default=None, ge=0)
    reason_code: str = Field(min_length=1, max_length=100)
    explanation: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_resolution(self) -> Self:
        """Reject privilege outcomes that contradict deterministic safety."""
        if not self.safety_allowed and self.status is not PrivilegeResolutionStatus.BLOCKED:
            raise ValueError("A safety block must remain BLOCKED")
        if self.status is PrivilegeResolutionStatus.REQUIRED and (
            self.requirement is not PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED
            or not self.preflight_complete
        ):
            raise ValueError("Administrator routing requires complete action-specific preflight")
        if self.requirement in {
            PrivilegeRequirement.SYSTEM_REQUIRED,
            PrivilegeRequirement.TRUSTED_INSTALLER_OR_UNSUPPORTED,
        } and self.status not in {
            PrivilegeResolutionStatus.UNSUPPORTED,
            PrivilegeResolutionStatus.BLOCKED,
        }:
            raise ValueError("SYSTEM and TrustedInstaller requirements are never executable")
        return self

    def canonical_digest(self) -> str:
        """Hash the exact routing conclusion and its non-secret evidence."""
        return canonical_model_digest(self.model_dump(mode="json"))


class ServiceStartPayload(FrozenModel):
    """Exact service identity and expected STOPPED state for a start request."""

    payload_type: Literal[PrivilegedActionType.SERVICE_START] = PrivilegedActionType.SERVICE_START
    action_schema_version: Literal[1] = SERVICE_CONTROL_SCHEMA_VERSION
    safety_policy_version: Literal["stage4x2-service-control-v1"] = SERVICE_CONTROL_POLICY_VERSION
    service_identity: ServiceStableIdentity
    expected_status: Literal[ServiceState.STOPPED] = ServiceState.STOPPED
    expected_startup_configuration_digest: Sha256Digest
    expected_dependency_digest: Sha256Digest


class ServiceStopPayload(FrozenModel):
    """Exact service identity and expected RUNNING state for a stop request."""

    payload_type: Literal[PrivilegedActionType.SERVICE_STOP] = PrivilegedActionType.SERVICE_STOP
    action_schema_version: Literal[1] = SERVICE_CONTROL_SCHEMA_VERSION
    safety_policy_version: Literal["stage4x2-service-control-v1"] = SERVICE_CONTROL_POLICY_VERSION
    service_identity: ServiceStableIdentity
    expected_status: Literal[ServiceState.RUNNING] = ServiceState.RUNNING
    expected_startup_configuration_digest: Sha256Digest
    expected_dependency_digest: Sha256Digest


class ServiceRestartPayload(FrozenModel):
    """Defined-only restart payload; Stage 4X1 does not register an executor."""

    payload_type: Literal[PrivilegedActionType.SERVICE_RESTART] = (
        PrivilegedActionType.SERVICE_RESTART
    )
    action_schema_version: Literal[1] = SERVICE_CONTROL_SCHEMA_VERSION
    safety_policy_version: Literal["stage4x2-service-control-v1"] = SERVICE_CONTROL_POLICY_VERSION
    service_identity: ServiceStableIdentity
    expected_status: Literal[ServiceState.RUNNING] = ServiceState.RUNNING
    expected_startup_configuration_digest: Sha256Digest
    expected_dependency_digest: Sha256Digest


class ServiceStartupTypeChangePayload(FrozenModel):
    """Exact Automatic/Manual startup-type request with verified backup evidence."""

    payload_type: Literal[PrivilegedActionType.SERVICE_STARTUP_TYPE_CHANGE] = (
        PrivilegedActionType.SERVICE_STARTUP_TYPE_CHANGE
    )
    action_schema_version: Literal[1] = SERVICE_STARTUP_SCHEMA_VERSION
    safety_policy_version: Literal["stage4x3-service-startup-v1"] = SERVICE_STARTUP_POLICY_VERSION
    source_transaction_id: UUID
    service_identity: ServiceStableIdentity
    expected_current_configuration: ServiceStartupConfiguration
    requested_startup_type: ServiceStartupType
    expected_runtime_state: ServiceState
    impact_digest: Sha256Digest
    safety_digest: Sha256Digest
    backup_id: UUID
    backup_digest: Sha256Digest

    @model_validator(mode="after")
    def enforce_stage_4c2_transition(self) -> Self:
        """Keep the defined payload inside the existing Automatic/Manual boundary."""
        if self.requested_startup_type not in {
            ServiceStartupType.AUTOMATIC,
            ServiceStartupType.MANUAL,
        }:
            raise ValueError("Only Automatic and Manual are represented by Stage 4X3")
        if (
            self.expected_current_configuration.startup_type
            not in {
                ServiceStartupType.AUTOMATIC,
                ServiceStartupType.MANUAL,
            }
            or self.expected_current_configuration.delayed_auto_start
        ):
            raise ValueError("Delayed, Disabled, Boot, System, and unknown types are blocked")
        if self.requested_startup_type is self.expected_current_configuration.startup_type:
            raise ValueError("A startup-type request must represent an actual change")
        return self


class ServiceStartupTypeRestorePayload(FrozenModel):
    """Conflict-checked restore of one Agent-owned startup-type change."""

    payload_type: Literal[PrivilegedActionType.SERVICE_STARTUP_TYPE_RESTORE] = (
        PrivilegedActionType.SERVICE_STARTUP_TYPE_RESTORE
    )
    action_schema_version: Literal[1] = SERVICE_STARTUP_SCHEMA_VERSION
    safety_policy_version: Literal["stage4x3-service-startup-v1"] = SERVICE_STARTUP_POLICY_VERSION
    source_transaction_id: UUID
    original_change_transaction_id: UUID
    service_identity: ServiceStableIdentity
    expected_current_configuration: ServiceStartupConfiguration
    target_original_configuration: ServiceStartupConfiguration
    expected_runtime_state: ServiceState
    impact_digest: Sha256Digest
    safety_digest: Sha256Digest
    backup_id: UUID
    backup_digest: Sha256Digest

    @model_validator(mode="after")
    def enforce_conflict_checked_restore(self) -> Self:
        """Limit restore to the same non-delayed Automatic/Manual boundary."""
        supported = {ServiceStartupType.AUTOMATIC, ServiceStartupType.MANUAL}
        if (
            self.expected_current_configuration.startup_type not in supported
            or self.target_original_configuration.startup_type not in supported
            or self.expected_current_configuration.delayed_auto_start
            or self.target_original_configuration.delayed_auto_start
        ):
            raise ValueError("Service startup restore escaped the Automatic/Manual boundary")
        if self.expected_current_configuration == self.target_original_configuration:
            raise ValueError("Service startup restore cannot be a no-op")
        return self


class StartupMachineDisablePayload(FrozenModel):
    """One exact HKLM Run identity and encrypted backup reference."""

    payload_type: Literal[PrivilegedActionType.STARTUP_MACHINE_DISABLE] = (
        PrivilegedActionType.STARTUP_MACHINE_DISABLE
    )
    action_schema_version: Literal[1] = MACHINE_STARTUP_SCHEMA_VERSION
    safety_policy_version: Literal["stage4x3-machine-startup-v1"] = MACHINE_STARTUP_POLICY_VERSION
    source_transaction_id: UUID
    source: Literal[StartupSource.HKLM_RUN] = StartupSource.HKLM_RUN
    registry_identity: RegistryStartupIdentity
    startup_identity_digest: Sha256Digest
    expected_state_digest: Sha256Digest
    safety_digest: Sha256Digest
    backup_id: UUID
    backup_digest: Sha256Digest

    @model_validator(mode="after")
    def enforce_exact_hklm_run(self) -> Self:
        """Block RunOnce, native-view ambiguity, and identity substitution."""
        _require_hklm_run_identity(self.registry_identity)
        if self.startup_identity_digest != _machine_startup_identity_digest(self.registry_identity):
            raise ValueError("Machine startup identity digest does not match")
        return self


class StartupMachineRestorePayload(FrozenModel):
    """Defined-only restore reference without a generic registry payload."""

    payload_type: Literal[PrivilegedActionType.STARTUP_MACHINE_RESTORE] = (
        PrivilegedActionType.STARTUP_MACHINE_RESTORE
    )
    action_schema_version: Literal[1] = MACHINE_STARTUP_SCHEMA_VERSION
    safety_policy_version: Literal["stage4x3-machine-startup-v1"] = MACHINE_STARTUP_POLICY_VERSION
    source_transaction_id: UUID
    original_disable_transaction_id: UUID
    source: Literal[StartupSource.HKLM_RUN] = StartupSource.HKLM_RUN
    registry_identity: RegistryStartupIdentity
    startup_identity_digest: Sha256Digest
    expected_state_digest: Sha256Digest
    safety_digest: Sha256Digest
    backup_id: UUID
    backup_digest: Sha256Digest

    @model_validator(mode="after")
    def enforce_exact_hklm_restore(self) -> Self:
        """Bind restore to one original view and registry identity."""
        _require_hklm_run_identity(self.registry_identity)
        if self.startup_identity_digest != _machine_startup_identity_digest(self.registry_identity):
            raise ValueError("Machine startup restore identity digest does not match")
        return self


class MachineMsiUninstallPayload(FrozenModel):
    """Exact machine MSI identity; no executable or argument field exists."""

    payload_type: Literal[PrivilegedActionType.MSI_UNINSTALL_MACHINE] = (
        PrivilegedActionType.MSI_UNINSTALL_MACHINE
    )
    action_schema_version: Literal[1] = MACHINE_MSI_SCHEMA_VERSION
    safety_policy_version: Literal["stage4x3-machine-msi-v1"] = MACHINE_MSI_POLICY_VERSION
    source_transaction_id: UUID
    product_code: str = Field(pattern=r"^\{[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}\}$")
    product_code_digest: Sha256Digest
    software_identity_digest: Sha256Digest
    metadata_digest: Sha256Digest
    capability_digest: Sha256Digest
    registration_digest: Sha256Digest
    execution_assessment_digest: Sha256Digest
    preflight_digest: Sha256Digest
    display_name: str = Field(min_length=1, max_length=1_000)
    display_version: str | None = Field(default=None, max_length=500)
    publisher: str = Field(min_length=1, max_length=1_000)
    install_context: Literal[MsiInstallContext.MACHINE] = MsiInstallContext.MACHINE
    scope: Literal[SoftwareScope.LOCAL_MACHINE] = SoftwareScope.LOCAL_MACHINE
    architecture: SoftwareArchitecture
    source_anchor_digest: Sha256Digest

    @model_validator(mode="after")
    def bind_product_code(self) -> Self:
        """Reject a ProductCode substituted after local MSI validation."""
        if self.product_code_digest != canonical_model_digest(self.product_code):
            raise ValueError("ProductCode digest does not match")
        return self


PrivilegedPayload = Annotated[
    ServiceStartPayload
    | ServiceStopPayload
    | ServiceRestartPayload
    | ServiceStartupTypeChangePayload
    | ServiceStartupTypeRestorePayload
    | StartupMachineDisablePayload
    | StartupMachineRestorePayload
    | MachineMsiUninstallPayload,
    Field(discriminator="payload_type"),
]


class PrivilegedActionPlan(FrozenModel):
    """Immutable preparation plan for one exact R3 semantic operation."""

    plan_id: UUID = Field(default_factory=uuid4)
    source_plan_id: UUID
    source_plan_hash: Sha256Digest
    action_type: PrivilegedActionType
    action_schema_version: int = Field(ge=1, le=100)
    safety_policy_version: PolicyVersion
    manifest_digest: Sha256Digest
    payload: PrivilegedPayload
    payload_digest: Sha256Digest
    target_identity_hash: Sha256Digest
    object_summary_digest: Sha256Digest
    risk_level: RiskLevel = RiskLevel.R3
    privilege_requirement: Literal[PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED] = (
        PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def bind_payload(self) -> Self:
        """Reject action substitution, payload drift, or a lowered risk label."""
        _require_utc(self.created_at, "Plan creation")
        if self.action_type is not self.payload.payload_type:
            raise ValueError("Action type and payload type differ")
        if self.action_schema_version != self.payload.action_schema_version:
            raise ValueError("Plan and payload action schema versions differ")
        if self.safety_policy_version != self.payload.safety_policy_version:
            raise ValueError("Plan and payload safety policy versions differ")
        if self.payload_digest != canonical_model_digest(self.payload.model_dump(mode="json")):
            raise ValueError("Payload digest does not match")
        if self.risk_level is not RiskLevel.R3:
            raise ValueError("Stage 4X1 privileged semantic actions remain R3")
        return self

    def canonical_digest(self) -> str:
        """Hash every field that defines the preparation plan."""
        return canonical_model_digest(self.model_dump(mode="json"))


class PrivilegedActionPreview(FrozenModel):
    """Fresh execution-mode-bound Preview shown before two confirmations."""

    preview_id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    plan_hash: Sha256Digest
    action_type: PrivilegedActionType
    action_schema_version: int = Field(ge=1, le=100)
    safety_policy_version: PolicyVersion
    manifest_digest: Sha256Digest
    target_identity_hash: Sha256Digest
    target_state_hash: Sha256Digest
    safety_digest: Sha256Digest
    risk_level: RiskLevel = RiskLevel.R3
    privilege_resolution: PrivilegeResolution
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    execution_mode: PrivilegedExecutionMode = PrivilegedExecutionMode.MOCK
    mock_only: bool = True
    warning: str = Field(
        default=(
            "Stage 4X1 validates a Mock Broker request only; no real elevated system "
            "operation will be performed."
        ),
        min_length=1,
        max_length=500,
    )

    @model_validator(mode="after")
    def validate_preview(self) -> Self:
        """Keep Preview routing, risk, and execution mode internally consistent."""
        _require_utc(self.generated_at, "Preview generation")
        if self.risk_level is not RiskLevel.R3:
            raise ValueError("Privileged Preview risk must remain R3")
        if (
            self.privilege_resolution.status is not PrivilegeResolutionStatus.REQUIRED
            or self.privilege_resolution.requirement
            is not PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED
            or not self.privilege_resolution.safety_allowed
        ):
            raise ValueError("Privileged Preview requires safety-approved Administrator evidence")
        if self.mock_only != (self.execution_mode is PrivilegedExecutionMode.MOCK):
            raise ValueError("Preview mock flag and execution mode differ")
        return self

    def canonical_digest(self) -> str:
        """Hash the exact fresh evidence displayed to the user."""
        return canonical_model_digest(self.model_dump(mode="json"))


class PrivilegedActionRequest(FrozenModel):
    """Short-lived single-target capability sent to the selected Broker boundary."""

    request_id: UUID = Field(default_factory=uuid4)
    protocol_version: Literal[2] = PROTOCOL_VERSION
    action_type: PrivilegedActionType
    action_schema_version: int = Field(ge=1, le=100)
    safety_policy_version: PolicyVersion
    manifest_digest: Sha256Digest
    payload: PrivilegedPayload
    payload_digest: Sha256Digest
    target_identity_hash: Sha256Digest
    plan_id: UUID
    plan_hash: Sha256Digest
    preview_id: UUID
    preview_hash: Sha256Digest
    plan_confirmation_id: UUID
    confirmation_id: UUID
    object_summary_digest: Sha256Digest
    risk_level: RiskLevel
    privilege_requirement: PrivilegeRequirement
    agent_instance_id: UUID
    caller_context_reference: UUID
    nonce: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_capability(self) -> Self:
        """Bind action, payload, risk, privilege, and a short positive lifetime."""
        _require_utc(self.created_at, "Request creation")
        _require_utc(self.expires_at, "Request expiry")
        if self.action_type is not self.payload.payload_type:
            raise ValueError("Action type and payload type differ")
        if self.action_schema_version != self.payload.action_schema_version:
            raise ValueError("Request and payload action schema versions differ")
        if self.safety_policy_version != self.payload.safety_policy_version:
            raise ValueError("Request and payload safety policy versions differ")
        if self.payload_digest != canonical_model_digest(self.payload.model_dump(mode="json")):
            raise ValueError("Payload digest does not match")
        if self.risk_level is not RiskLevel.R3:
            raise ValueError("Privileged requests must retain the approved R3 risk")
        if self.privilege_requirement is not PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED:
            raise ValueError("Only explicit Administrator-required requests may be built")
        if self.expires_at <= self.created_at:
            raise ValueError("Privileged request expiry must be after creation")
        if (self.expires_at - self.created_at).total_seconds() > 600:
            raise ValueError("Privileged request lifetime cannot exceed ten minutes")
        return self


class IntegrityAlgorithm(StrEnum):
    """Algorithms implemented by the Stage 4X1 authenticator abstraction."""

    HMAC_SHA256 = "HMAC-SHA256"


class RequestIntegrity(FrozenModel):
    """Authentication metadata kept separate from the request digest."""

    algorithm: Literal[IntegrityAlgorithm.HMAC_SHA256] = IntegrityAlgorithm.HMAC_SHA256
    key_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    authentication_code: str = Field(pattern=r"^[0-9a-f]{64}$")


class PrivilegedActionEnvelope(FrozenModel):
    """Transport envelope containing one strict request and its integrity proof."""

    request: PrivilegedActionRequest
    request_digest: Sha256Digest
    integrity: RequestIntegrity


class PrivilegedCallerContext(FrozenModel):
    """Authenticated context supplied out-of-band by the selected Broker transport."""

    context_id: UUID
    agent_instance_id: UUID
    user_sid_fingerprint: Sha256Digest
    session_fingerprint: Sha256Digest


class PrivilegedReplayState(StrEnum):
    """Durable request states used for replay protection."""

    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    CONSUMING = "CONSUMING"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class PrivilegedTransactionState(StrEnum):
    """Durable end-to-end lifecycle; no interrupted state auto-resumes."""

    AWAITING_PLAN_CONFIRMATION = "AWAITING_PLAN_CONFIRMATION"
    PLAN_CONFIRMED = "PLAN_CONFIRMED"
    AWAITING_RUNTIME_CONFIRMATION = "AWAITING_RUNTIME_CONFIRMATION"
    AUTHORIZED = "AUTHORIZED"
    SIGNED = "SIGNED"
    VALIDATING = "VALIDATING"
    CONSUMING = "CONSUMING"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    INTERRUPTED = "INTERRUPTED"


class BrokerDecision(StrEnum):
    """Stable Broker validation decisions, separate from execution outcome."""

    APPROVED_FOR_MOCK_EXECUTION = "APPROVED_FOR_MOCK_EXECUTION"
    APPROVED_FOR_REAL_EXECUTION = "APPROVED_FOR_REAL_EXECUTION"
    REJECTED = "REJECTED"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    UNSUPPORTED_PROTOCOL_VERSION = "UNSUPPORTED_PROTOCOL_VERSION"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    INTEGRITY_INVALID = "INTEGRITY_INVALID"
    REQUEST_EXPIRED = "REQUEST_EXPIRED"
    REPLAY_REJECTED = "REPLAY_REJECTED"
    ACTION_NOT_ALLOWLISTED = "ACTION_NOT_ALLOWLISTED"
    ACTION_SCHEMA_UNSUPPORTED = "ACTION_SCHEMA_UNSUPPORTED"
    POLICY_VERSION_MISMATCH = "POLICY_VERSION_MISMATCH"
    MANIFEST_CHANGED = "MANIFEST_CHANGED"
    BACKUP_INVALID = "BACKUP_INVALID"
    BACKUP_BINDING_INVALID = "BACKUP_BINDING_INVALID"
    RESTORE_CONFLICT = "RESTORE_CONFLICT"
    REGISTRY_VIEW_CHANGED = "REGISTRY_VIEW_CHANGED"
    MSI_REGISTRATION_CHANGED = "MSI_REGISTRATION_CHANGED"
    SYSTEM_IDENTITY_REJECTED = "SYSTEM_IDENTITY_REJECTED"
    CONFIRMATION_INVALID = "CONFIRMATION_INVALID"
    PLAN_BINDING_INVALID = "PLAN_BINDING_INVALID"
    PREVIEW_BINDING_INVALID = "PREVIEW_BINDING_INVALID"
    CALLER_INVALID = "CALLER_INVALID"
    PRIVILEGE_UNSUPPORTED = "PRIVILEGE_UNSUPPORTED"
    TARGET_CHANGED = "TARGET_CHANGED"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    RISK_CHANGED = "RISK_CHANGED"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    PERSISTENCE_UNAVAILABLE = "PERSISTENCE_UNAVAILABLE"
    ELEVATION_CANCELLED = "ELEVATION_CANCELLED"
    BROKER_UNTRUSTED = "BROKER_UNTRUSTED"
    BROKER_NOT_ELEVATED = "BROKER_NOT_ELEVATED"
    IPC_AUTHENTICATION_FAILED = "IPC_AUTHENTICATION_FAILED"
    IPC_PROTOCOL_REJECTED = "IPC_PROTOCOL_REJECTED"
    IPC_DISCONNECTED = "IPC_DISCONNECTED"
    IPC_TIMED_OUT = "IPC_TIMED_OUT"
    RESULT_AUTHENTICATION_FAILED = "RESULT_AUTHENTICATION_FAILED"
    CLIENT_VERIFICATION_FAILED = "CLIENT_VERIFICATION_FAILED"


class MockExecutionStatus(StrEnum):
    """Outcome of the fake executor after Broker approval."""

    NOT_STARTED = "NOT_STARTED"
    MOCK_VALIDATED = "MOCK_VALIDATED"
    OPERATION_FAILED = "OPERATION_FAILED"


class PrivilegedVerificationStatus(StrEnum):
    """Fresh fake-state verification result."""

    NOT_RUN = "NOT_RUN"
    VERIFIED = "VERIFIED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class PrivilegedActionResult(FrozenModel):
    """Truthful Broker decision, Mock outcome, and fresh verification evidence."""

    request_id: UUID | None = None
    action_type: PrivilegedActionType | None = None
    broker_decision: BrokerDecision
    execution_started: bool = False
    execution_completed: bool = False
    execution_status: MockExecutionStatus = MockExecutionStatus.NOT_STARTED
    verification_status: PrivilegedVerificationStatus = PrivilegedVerificationStatus.NOT_RUN
    pre_state_hash: Sha256Digest | None = None
    post_state_hash: Sha256Digest | None = None
    result_code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1_000)
    started_at: datetime | None = None
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    audit_id: UUID | None = None

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        """Reject internally contradictory execution and verification claims."""
        _require_utc(self.completed_at, "Result completion")
        if self.started_at is not None:
            _require_utc(self.started_at, "Result start")
        if self.execution_completed and not self.execution_started:
            raise ValueError("Completed execution must also be marked started")
        if (
            not self.execution_started
            and self.execution_status is not MockExecutionStatus.NOT_STARTED
        ):
            raise ValueError("A non-started operation cannot have an execution outcome")
        if (
            self.verification_status is not PrivilegedVerificationStatus.NOT_RUN
            and not self.execution_completed
        ):
            raise ValueError("Verification requires completed Mock execution")
        if (
            self.verification_status is PrivilegedVerificationStatus.VERIFIED
            and self.post_state_hash is None
        ):
            raise ValueError("Verified results require fresh post-state evidence")
        return self


class PrivilegedActionResultEnvelope(FrozenModel):
    """Versioned authenticated result returned by a Mock or elevated Broker."""

    protocol_version: Literal[2] = PROTOCOL_VERSION
    result: PrivilegedActionResult
    result_digest: Sha256Digest
    integrity: RequestIntegrity


def canonical_model_digest(value: object) -> str:
    """Hash a JSON-compatible model value for local binding fields."""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require_utc(value: datetime, label: str) -> None:
    """Require explicit UTC so cross-process canonical hashes cannot vary by offset."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} timestamp must be timezone-aware UTC")


def _require_hklm_run_identity(identity: RegistryStartupIdentity) -> None:
    """Require the sole machine-startup registry key and an explicit registry view."""
    expected_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    if (
        identity.hive != "HKLM"
        or identity.key_path.casefold() != expected_key.casefold()
        or identity.registry_view not in {"32", "64"}
    ):
        raise ValueError("Only explicit 32/64-bit HKLM Run identities are supported")


def _machine_startup_identity_digest(identity: RegistryStartupIdentity) -> str:
    return StartupIdentity(source=StartupSource.HKLM_RUN, registry=identity).canonical_digest()
