"""Strict provider-neutral contracts for the Stage 4X2 one-shot Windows Broker."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, model_validator

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.privileged_actions import (
    IntegrityAlgorithm,
    PrivilegedActionType,
    RequestIntegrity,
)
from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.domain.service_actions import (
    ServiceStartupConfiguration,
    ServiceState,
)

BROKER_TRANSPORT_VERSION: Literal[1] = 1
Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
OpaqueId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{43}$")]


class BrokerTrustMode(StrEnum):
    """Binary trust policies available to the launcher."""

    PRODUCTION = "PRODUCTION"
    DEVELOPMENT = "DEVELOPMENT"


class SignatureStatus(StrEnum):
    """Offline Authenticode conclusion used by the trust policy."""

    VALID = "VALID"
    UNSIGNED = "UNSIGNED"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class BrokerLifecycleState(StrEnum):
    """Durable one-shot Broker lifecycle; terminal states never resume."""

    PREPARED = "PREPARED"
    LAUNCHING = "LAUNCHING"
    UAC_PENDING = "UAC_PENDING"
    BROKER_STARTING = "BROKER_STARTING"
    HANDSHAKING = "HANDSHAKING"
    AUTHENTICATED = "AUTHENTICATED"
    REQUEST_VALIDATED = "REQUEST_VALIDATED"
    CONSUMING = "CONSUMING"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RESULT_SENT = "RESULT_SENT"
    EXITED = "EXITED"
    ELEVATION_CANCELLED = "ELEVATION_CANCELLED"
    REJECTED = "REJECTED"
    TIMED_OUT = "TIMED_OUT"
    DISCONNECTED = "DISCONNECTED"
    INTERRUPTED = "INTERRUPTED"


class BrokerFailureCode(StrEnum):
    """Stable fail-closed Stage 4X2 failure codes shown without sensitive details."""

    ELEVATION_CANCELLED = "ELEVATION_CANCELLED"
    BROKER_NOT_CONFIGURED = "BROKER_NOT_CONFIGURED"
    BROKER_UNTRUSTED = "BROKER_UNTRUSTED"
    BROKER_NOT_ELEVATED = "BROKER_NOT_ELEVATED"
    DIFFERENT_ELEVATION_ACCOUNT = "DIFFERENT_ELEVATION_ACCOUNT"
    CALLER_IDENTITY_MISMATCH = "CALLER_IDENTITY_MISMATCH"
    SESSION_MISMATCH = "SESSION_MISMATCH"
    PROCESS_IDENTITY_MISMATCH = "PROCESS_IDENTITY_MISMATCH"
    IPC_ENDPOINT_TAKEN = "IPC_ENDPOINT_TAKEN"
    IPC_PROTOCOL_REJECTED = "IPC_PROTOCOL_REJECTED"
    IPC_AUTHENTICATION_FAILED = "IPC_AUTHENTICATION_FAILED"
    IPC_TIMED_OUT = "IPC_TIMED_OUT"
    IPC_DISCONNECTED = "IPC_DISCONNECTED"
    REQUEST_REJECTED = "REQUEST_REJECTED"
    PERSISTENCE_UNAVAILABLE = "PERSISTENCE_UNAVAILABLE"
    TARGET_CHANGED = "TARGET_CHANGED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    RESULT_AUTHENTICATION_FAILED = "RESULT_AUTHENTICATION_FAILED"


class IpcMessageType(StrEnum):
    """Finite message sequence supported by the authenticated pipe protocol."""

    BROKER_READY = "BROKER_READY"
    CLIENT_HELLO = "CLIENT_HELLO"
    BROKER_CHALLENGE = "BROKER_CHALLENGE"
    SESSION_GRANT = "SESSION_GRANT"
    CLIENT_PROOF = "CLIENT_PROOF"
    REQUEST = "REQUEST"
    RESULT = "RESULT"
    ERROR = "ERROR"


class ElevatedExecutionStatus(StrEnum):
    """Truthful outcome of the exact SCM dispatch."""

    NOT_STARTED = "NOT_STARTED"
    DISPATCHED = "DISPATCHED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class ElevatedVerificationStatus(StrEnum):
    """Broker-side postcondition verification conclusion."""

    NOT_RUN = "NOT_RUN"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"


class WindowsProcessIdentity(FrozenModel):
    """OS-derived identity for one endpoint process; payload claims never create it."""

    user_sid: str = Field(pattern=r"^S-\d(?:-\d+)+$", max_length=184)
    session_id: int = Field(ge=0)
    process_id: int = Field(ge=1)
    process_creation_time_ns: int = Field(ge=1)
    image_path_hash: Sha256Digest
    image_sha256: Sha256Digest
    product_version: str | None = Field(default=None, max_length=100)
    elevated: bool
    integrity_level: str = Field(min_length=1, max_length=40)

    def canonical_digest(self) -> str:
        """Hash the complete OS-derived endpoint identity."""
        return canonical_broker_digest(self.model_dump(mode="json"))


class BrokerBinaryIdentity(FrozenModel):
    """Pre-launch file evidence for the exact independent Broker executable."""

    path_hash: Sha256Digest
    file_id: str = Field(min_length=1, max_length=160)
    sha256: Sha256Digest
    size_bytes: int = Field(ge=1)
    product_version: str | None = Field(default=None, max_length=100)
    signature_status: SignatureStatus
    signer_fingerprint: Sha256Digest | None = None
    trusted_location: bool
    reparse_point: bool = False
    manifest_execution_level: Literal["asInvoker"] = "asInvoker"

    def canonical_digest(self) -> str:
        """Hash every property used by the pre-launch trust decision."""
        return canonical_broker_digest(self.model_dump(mode="json"))


class BrokerLaunchTicket(FrozenModel):
    """Short-lived in-memory launch binding; only opaque IDs enter the command line."""

    broker_instance_id: UUID = Field(default_factory=uuid4)
    rendezvous_id: OpaqueId
    protocol_version: Literal[1] = BROKER_TRANSPORT_VERSION
    request_id: UUID
    request_digest: Sha256Digest
    agent_instance_id: UUID
    caller_identity_digest: Sha256Digest
    broker_identity_digest: Sha256Digest
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_lifetime(self) -> Self:
        """Require UTC and a narrowly bounded ticket lifetime."""
        _require_utc(self.created_at, "Launch ticket creation")
        _require_utc(self.expires_at, "Launch ticket expiry")
        lifetime = self.expires_at - self.created_at
        if lifetime <= timedelta(0) or lifetime > timedelta(minutes=10):
            raise ValueError("Broker launch ticket lifetime must be between zero and ten minutes")
        return self

    def canonical_digest(self) -> str:
        """Hash the complete pre-UAC launch binding."""
        return canonical_broker_digest(self.model_dump(mode="json"))


class IpcFrame(FrozenModel):
    """One bounded message frame before the four-byte length prefix is applied."""

    protocol_version: Literal[1] = BROKER_TRANSPORT_VERSION
    message_type: IpcMessageType
    broker_instance_id: UUID
    request_id: UUID
    sequence: int = Field(ge=0, le=16)
    sent_at: datetime
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    payload_digest: Sha256Digest
    integrity: RequestIntegrity | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        """Reject a frame whose payload digest or time encoding is ambiguous."""
        _require_utc(self.sent_at, "IPC frame")
        if self.payload_digest != canonical_broker_digest(self.payload):
            raise ValueError("IPC payload digest does not match")
        if self.sequence <= 2 and self.integrity is not None:
            raise ValueError("Pre-session handshake frames cannot claim session integrity")
        if self.sequence >= 3 and self.integrity is None:
            raise ValueError("Authenticated IPC frames require integrity metadata")
        return self

    def unsigned_bytes(self) -> bytes:
        """Return canonical bytes excluding the integrity field for HMAC."""
        data = self.model_dump(mode="json", exclude={"integrity"})
        return canonical_broker_bytes(data)


class BrokerReady(FrozenModel):
    """Unauthenticated bounded readiness message subsequently bound by the transcript."""

    broker_instance_id: UUID
    protocol_version: Literal[1] = BROKER_TRANSPORT_VERSION
    broker_process_id: int = Field(ge=1)
    broker_session_id: int = Field(ge=0)
    broker_version: str = Field(min_length=1, max_length=100)
    broker_identity_digest: Sha256Digest
    broker_challenge: OpaqueId


class ClientHello(FrozenModel):
    """Main-process identity and fresh challenge derived before request disclosure."""

    agent_instance_id: UUID
    application_version: str = Field(min_length=1, max_length=100)
    caller_identity: WindowsProcessIdentity
    launch_ticket_digest: Sha256Digest
    client_challenge: OpaqueId


class SessionGrant(FrozenModel):
    """Ephemeral HMAC key delivered only after both OS endpoint identities pass."""

    session_key: OpaqueId = Field(repr=False)
    transcript_digest: Sha256Digest
    expires_at: datetime

    @model_validator(mode="after")
    def require_utc_expiry(self) -> Self:
        """Require an unambiguous UTC session expiry."""
        _require_utc(self.expires_at, "IPC session expiry")
        return self


class ClientProof(FrozenModel):
    """Proof that the expected client received the one-time session grant."""

    transcript_digest: Sha256Digest
    proof: str = Field(pattern=r"^[0-9a-f]{64}$")


class ServiceControlResultEvidence(FrozenModel):
    """Exact runtime-state evidence for the original Stage 4X2 service actions."""

    evidence_type: Literal["SERVICE_CONTROL"] = "SERVICE_CONTROL"
    before_state: ServiceState
    after_state: ServiceState


class ServiceStartupResultEvidence(FrozenModel):
    """Startup configuration readback with proof that runtime state did not change."""

    evidence_type: Literal["SERVICE_STARTUP"] = "SERVICE_STARTUP"
    before_configuration: ServiceStartupConfiguration
    after_configuration: ServiceStartupConfiguration
    before_runtime_state: ServiceState
    after_runtime_state: ServiceState
    runtime_unchanged: bool


class MachineStartupResultEvidence(FrozenModel):
    """Presence-only HKLM Run evidence; raw registry data never crosses IPC."""

    evidence_type: Literal["MACHINE_STARTUP"] = "MACHINE_STARTUP"
    registry_view: Literal["32", "64"]
    value_present_before: bool
    value_present_after: bool


class MachineMsiResultEvidence(FrozenModel):
    """Machine MSI dispatch and fresh registration evidence without command metadata."""

    evidence_type: Literal["MACHINE_MSI"] = "MACHINE_MSI"
    installer_category: str = Field(min_length=1, max_length=100)
    exit_code: int | None = Field(default=None, ge=0, le=0xFFFFFFFF)
    monitoring_detached: bool = False
    product_registration_present_after: bool | None = None
    software_identity_present_after: bool | None = None


BrokerActionEvidence = Annotated[
    ServiceControlResultEvidence
    | ServiceStartupResultEvidence
    | MachineStartupResultEvidence
    | MachineMsiResultEvidence,
    Field(discriminator="evidence_type"),
]


class ElevatedBrokerResult(FrozenModel):
    """Authenticated Broker decision and action-specific postcondition evidence."""

    request_id: UUID
    broker_instance_id: UUID
    action_type: PrivilegedActionType
    target_identity_hash: Sha256Digest
    decision: str = Field(min_length=1, max_length=100)
    execution_status: ElevatedExecutionStatus
    verification_status: ElevatedVerificationStatus
    execution_started: bool = False
    pre_state: ServiceState | None = None
    post_state: ServiceState | None = None
    pre_state_hash: Sha256Digest | None = None
    post_state_hash: Sha256Digest | None = None
    action_evidence: BrokerActionEvidence | None = None
    result_code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1_000)
    started_at: datetime | None = None
    completed_at: datetime
    rollback_level: RollbackLevel = RollbackLevel.MANUAL

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        """Reject unsupported actions and contradictory execution claims."""
        _require_utc(self.completed_at, "Broker result completion")
        if self.started_at is not None:
            _require_utc(self.started_at, "Broker result start")
        if (
            not self.execution_started
            and self.execution_status is not ElevatedExecutionStatus.NOT_STARTED
        ):
            raise ValueError("A non-started result cannot claim an execution outcome")
        if self.verification_status is ElevatedVerificationStatus.VERIFIED and (
            not self.execution_started
            or self.post_state_hash is None
            or self.action_evidence is None
        ):
            raise ValueError("Verified result requires execution and post-state evidence")
        if self.action_evidence is not None:
            expected_evidence = {
                PrivilegedActionType.SERVICE_START: "SERVICE_CONTROL",
                PrivilegedActionType.SERVICE_STOP: "SERVICE_CONTROL",
                PrivilegedActionType.SERVICE_STARTUP_TYPE_CHANGE: "SERVICE_STARTUP",
                PrivilegedActionType.SERVICE_STARTUP_TYPE_RESTORE: "SERVICE_STARTUP",
                PrivilegedActionType.STARTUP_MACHINE_DISABLE: "MACHINE_STARTUP",
                PrivilegedActionType.STARTUP_MACHINE_RESTORE: "MACHINE_STARTUP",
                PrivilegedActionType.MSI_UNINSTALL_MACHINE: "MACHINE_MSI",
            }.get(self.action_type)
            if expected_evidence != self.action_evidence.evidence_type:
                raise ValueError("Broker result evidence does not match its action type")
        return self

    def canonical_digest(self) -> str:
        """Hash the complete result before transport authentication."""
        return canonical_broker_digest(self.model_dump(mode="json"))


class ElevatedBrokerResultEnvelope(FrozenModel):
    """Session-authenticated Stage 4X3 result envelope."""

    protocol_version: Literal[1] = BROKER_TRANSPORT_VERSION
    broker_instance_id: UUID
    request_id: UUID
    result: ElevatedBrokerResult
    result_digest: Sha256Digest
    integrity: RequestIntegrity

    @model_validator(mode="after")
    def bind_result(self) -> Self:
        """Bind outer routing fields and digest to the exact inner result."""
        if (
            self.broker_instance_id != self.result.broker_instance_id
            or self.request_id != self.result.request_id
            or self.result_digest != self.result.canonical_digest()
            or self.integrity.algorithm is not IntegrityAlgorithm.HMAC_SHA256
        ):
            raise ValueError("Broker result envelope binding differs")
        return self


def canonical_broker_bytes(value: object) -> bytes:
    """Encode one JSON-compatible Broker value without ambiguous whitespace or NaN."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_broker_digest(value: object) -> str:
    """Return SHA-256 over canonical Stage 4X2 JSON bytes."""
    return hashlib.sha256(canonical_broker_bytes(value)).hexdigest()


def _require_utc(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must use UTC")


def utc_now() -> datetime:
    """Return an aware UTC timestamp for dependency injection defaults."""
    return datetime.now(UTC)
