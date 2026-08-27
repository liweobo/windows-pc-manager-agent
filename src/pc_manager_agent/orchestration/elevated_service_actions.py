"""Standard-user UAC and authenticated Broker coordination for Stage 4X2."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Protocol
from uuid import UUID, uuid4

from pc_manager_agent import __version__
from pc_manager_agent.audit.elevated_broker import (
    ElevatedBrokerAuditContext,
    ElevatedBrokerAuditLogger,
)
from pc_manager_agent.audit.repository import AuditUnavailableError
from pc_manager_agent.domain.elevated_broker import (
    BrokerFailureCode,
    BrokerLaunchTicket,
    BrokerLifecycleState,
    BrokerTrustMode,
    ElevatedBrokerResultEnvelope,
    WindowsProcessIdentity,
)
from pc_manager_agent.domain.privileged_actions import (
    PrivilegedActionEnvelope,
    PrivilegedReplayState,
    PrivilegedTransactionState,
    ServiceStartPayload,
    ServiceStopPayload,
)
from pc_manager_agent.domain.service_actions import ServiceState
from pc_manager_agent.persistence.privileged_actions import (
    PrivilegedActionRepository,
    PrivilegedActionStoreError,
    PrivilegedReplayError,
)
from pc_manager_agent.platform_support.privileged_broker import (
    BrokerBinaryInspector,
    BrokerLaunchArguments,
    BrokerPipeClient,
    ElevatedBrokerLauncher,
    ElevationLaunchStatus,
)
from pc_manager_agent.platform_support.service_control import ServiceControlPlatform
from pc_manager_agent.privileged.broker_identity import BrokerTrustError, BrokerTrustPolicy
from pc_manager_agent.privileged.broker_session import ElevatedBrokerClientSession
from pc_manager_agent.privileged.ipc_protocol import BrokerFrameCodec, BrokerIpcError, new_opaque_id
from pc_manager_agent.privileged.serialization import PrivilegedRequestSerializer


class ElevatedDispatchStatus(StrEnum):
    """Main-process conclusion after UAC, IPC, Broker, and independent readback."""

    VERIFIED = "VERIFIED"
    ELEVATION_CANCELLED = "ELEVATION_CANCELLED"
    REJECTED = "REJECTED"
    INTERRUPTED = "INTERRUPTED"
    CLIENT_VERIFICATION_FAILED = "CLIENT_VERIFICATION_FAILED"


@dataclass(frozen=True, slots=True)
class ElevatedDispatchOutcome:
    """Truthful UI-facing outcome; Broker result remains separately authenticated."""

    status: ElevatedDispatchStatus
    result: ElevatedBrokerResultEnvelope | None
    failure_code: BrokerFailureCode | None
    message: str


class BrokerPipeClientFactory(Protocol):
    """Create one connector for an opaque Broker rendezvous identifier."""

    def __call__(self, rendezvous_id: str) -> BrokerPipeClient:
        """Return a connector without opening or discovering another endpoint."""
        ...


class ElevatedServiceActionCoordinator:
    """Launch and communicate with one configured Broker without blocking safety gates."""

    def __init__(
        self,
        *,
        broker_path: Path,
        expected_broker_sha256: str,
        trust_mode: BrokerTrustMode,
        caller_identity: WindowsProcessIdentity,
        launcher: ElevatedBrokerLauncher,
        binary_inspector: BrokerBinaryInspector,
        pipe_client_factory: BrokerPipeClientFactory,
        serializer: PrivilegedRequestSerializer,
        repository: PrivilegedActionRepository,
        audit: ElevatedBrokerAuditLogger,
        service_platform: ServiceControlPlatform,
        connect_timeout_seconds: float = 30.0,
        message_timeout_seconds: float = 15.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if caller_identity.elevated:
            raise ValueError("The main Agent cannot create an elevated Broker coordinator")
        self._path = broker_path
        self._expected_sha = expected_broker_sha256
        self._trust = BrokerTrustPolicy(trust_mode, expected_broker_sha256)
        self._caller = caller_identity
        self._launcher = launcher
        self._inspector = binary_inspector
        self._pipe_factory = pipe_client_factory
        self._serializer = serializer
        self._repository = repository
        self._audit = audit
        self._platform = service_platform
        self._connect_timeout = connect_timeout_seconds
        self._message_timeout = message_timeout_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self._active_lock = Lock()
        self._active_requests: set[UUID] = set()

    def dispatch(self, envelope: PrivilegedActionEnvelope) -> ElevatedDispatchOutcome:
        """Request UAC once, exchange one request, verify once locally, and never retry."""
        request = envelope.request
        with self._active_lock:
            if request.request_id in self._active_requests:
                return ElevatedDispatchOutcome(
                    ElevatedDispatchStatus.REJECTED,
                    None,
                    BrokerFailureCode.REQUEST_REJECTED,
                    "This exact request already has an active UAC/Broker attempt",
                )
            self._active_requests.add(request.request_id)
        try:
            return self._dispatch_once(envelope)
        finally:
            with self._active_lock:
                self._active_requests.discard(request.request_id)

    def _dispatch_once(self, envelope: PrivilegedActionEnvelope) -> ElevatedDispatchOutcome:
        """Perform one guarded UAC and Broker attempt without retry."""
        request = envelope.request
        broker_instance_id = uuid4()
        rendezvous_id = new_opaque_id()
        try:
            snapshot = self._repository.snapshot(request)
            if (
                snapshot.transaction_state is not PrivilegedTransactionState.SIGNED
                or snapshot.replay_state is not PrivilegedReplayState.CREATED
                or snapshot.stored_request_digest != envelope.request_digest
                or self._now() >= request.expires_at
            ):
                raise BrokerTrustError("Privileged request is stale, consumed, or not signed")
            binary = self._inspector.inspect(self._path)
            if binary.sha256 != self._expected_sha:
                raise BrokerTrustError("Configured Broker SHA-256 changed")
            self._trust.require_binary(binary)
            audit_context = ElevatedBrokerAuditContext(
                caller_sid_fingerprint=hashlib.sha256(
                    self._caller.user_sid.encode("utf-8")
                ).hexdigest(),
                session_id=self._caller.session_id,
                broker_executable_identity=binary.canonical_digest(),
                broker_version=__version__,
                ipc_endpoint_fingerprint=hashlib.sha256(rendezvous_id.encode("ascii")).hexdigest(),
            )
            ticket = BrokerLaunchTicket(
                broker_instance_id=broker_instance_id,
                rendezvous_id=rendezvous_id,
                request_id=request.request_id,
                request_digest=envelope.request_digest,
                agent_instance_id=request.agent_instance_id,
                caller_identity_digest=self._caller.canonical_digest(),
                broker_identity_digest=binary.canonical_digest(),
                created_at=self._now(),
                expires_at=request.expires_at,
            )
            self._audit.lifecycle(
                state=BrokerLifecycleState.UAC_PENDING,
                broker_instance_id=broker_instance_id,
                request_id=request.request_id,
                plan_id=request.plan_id,
                context=audit_context,
                uac_result="REQUESTED",
                handshake_result="NOT_STARTED",
                final_transaction_state="AWAITING_UAC",
            )
        except (
            AuditUnavailableError,
            BrokerTrustError,
            OSError,
            PrivilegedActionStoreError,
            PrivilegedReplayError,
            ValueError,
        ):
            self._invalidate_unlaunched(request.request_id, "BROKER_PRELAUNCH_REJECTED")
            return ElevatedDispatchOutcome(
                ElevatedDispatchStatus.REJECTED,
                None,
                BrokerFailureCode.BROKER_UNTRUSTED,
                "Broker binary trust or mandatory pre-launch audit failed",
            )
        try:
            launch = self._launcher.launch(
                self._path,
                BrokerLaunchArguments(
                    broker_instance_id=broker_instance_id,
                    rendezvous_id=rendezvous_id,
                    protocol_version=ticket.protocol_version,
                    expected_caller_process_id=self._caller.process_id,
                    agent_instance_id=request.agent_instance_id,
                ),
            )
        except (OSError, RuntimeError, ValueError):
            self._invalidate_unlaunched(request.request_id, "ELEVATION_LAUNCH_FAILED")
            self._record_terminal(
                BrokerLifecycleState.REJECTED,
                envelope,
                broker_instance_id,
                BrokerFailureCode.BROKER_UNTRUSTED,
                context=audit_context,
                uac_result="FAILED",
                handshake_result="NOT_STARTED",
                broker_exit_code="NOT_STARTED",
                final_transaction_state="REJECTED",
            )
            return ElevatedDispatchOutcome(
                ElevatedDispatchStatus.REJECTED,
                None,
                BrokerFailureCode.BROKER_UNTRUSTED,
                "Windows could not start the trusted one-shot Broker",
            )
        if launch.status is ElevationLaunchStatus.CANCELLED:
            self._invalidate_unlaunched(request.request_id, "ELEVATION_CANCELLED")
            self._record_terminal(
                BrokerLifecycleState.ELEVATION_CANCELLED,
                envelope,
                broker_instance_id,
                BrokerFailureCode.ELEVATION_CANCELLED,
                context=audit_context,
                uac_result="CANCELLED",
                handshake_result="NOT_STARTED",
                broker_exit_code="NOT_STARTED",
                final_transaction_state="REJECTED",
            )
            return ElevatedDispatchOutcome(
                ElevatedDispatchStatus.ELEVATION_CANCELLED,
                None,
                BrokerFailureCode.ELEVATION_CANCELLED,
                "Windows administrator confirmation was cancelled; nothing was executed",
            )
        if launch.status is not ElevationLaunchStatus.STARTED or launch.process is None:
            self._invalidate_unlaunched(request.request_id, "ELEVATION_LAUNCH_FAILED")
            self._record_terminal(
                BrokerLifecycleState.REJECTED,
                envelope,
                broker_instance_id,
                BrokerFailureCode.BROKER_UNTRUSTED,
                context=audit_context,
                uac_result="FAILED",
                handshake_result="NOT_STARTED",
                broker_exit_code="UNKNOWN",
                final_transaction_state="REJECTED",
            )
            return ElevatedDispatchOutcome(
                ElevatedDispatchStatus.REJECTED,
                None,
                BrokerFailureCode.BROKER_UNTRUSTED,
                "Windows could not start the trusted one-shot Broker",
            )
        stream = None
        try:
            self._audit.lifecycle(
                state=BrokerLifecycleState.BROKER_STARTING,
                broker_instance_id=broker_instance_id,
                request_id=request.request_id,
                plan_id=request.plan_id,
                context=audit_context,
                uac_result="APPROVED",
                handshake_result="PENDING",
                broker_exit_code="NOT_WAITED",
                final_transaction_state="BROKER_STARTING",
            )
            pipe_client = self._pipe_factory(rendezvous_id)
            stream = pipe_client.connect(timeout_seconds=self._connect_timeout)
            client = ElevatedBrokerClientSession(
                self._caller,
                binary,
                launch.process.process_id,
                self._serializer,
                codec=BrokerFrameCodec(self._serializer.max_request_bytes),
                message_timeout_seconds=self._message_timeout,
            )
            result = client.exchange(
                stream,
                envelope,
                broker_instance_id=broker_instance_id,
                launch_ticket_digest=ticket.canonical_digest(),
                agent_instance_id=request.agent_instance_id,
            )
            main_readback_verified = self._verify_client_postcondition(envelope)
            broker_exit_code = self._launcher.wait_for_exit(
                launch.process,
                timeout_seconds=self._message_timeout,
            )
            exit_evidence = "TIMED_OUT" if broker_exit_code is None else str(broker_exit_code)
            if not main_readback_verified:
                self._record_terminal(
                    BrokerLifecycleState.REJECTED,
                    envelope,
                    broker_instance_id,
                    BrokerFailureCode.VERIFICATION_FAILED,
                    context=audit_context,
                    uac_result="APPROVED",
                    handshake_result="AUTHENTICATED",
                    main_readback_result="FAILED",
                    result_integrity_result="PASSED",
                    broker_exit_code=exit_evidence,
                    final_transaction_state="COMPLETED_UNVERIFIED",
                )
                return ElevatedDispatchOutcome(
                    ElevatedDispatchStatus.CLIENT_VERIFICATION_FAILED,
                    result,
                    BrokerFailureCode.VERIFICATION_FAILED,
                    "Broker replied, but the standard-user fresh service readback did not agree",
                )
            if broker_exit_code is None:
                self._record_terminal(
                    BrokerLifecycleState.TIMED_OUT,
                    envelope,
                    broker_instance_id,
                    BrokerFailureCode.IPC_TIMED_OUT,
                    context=audit_context,
                    uac_result="APPROVED",
                    handshake_result="AUTHENTICATED",
                    main_readback_result="PASSED",
                    result_integrity_result="PASSED",
                    broker_exit_code="TIMED_OUT_NOT_TERMINATED",
                    final_transaction_state="COMPLETED_UNVERIFIED",
                )
                return ElevatedDispatchOutcome(
                    ElevatedDispatchStatus.CLIENT_VERIFICATION_FAILED,
                    result,
                    BrokerFailureCode.IPC_TIMED_OUT,
                    "The service state verified, but the elevated Broker did not exit in time",
                )
            if broker_exit_code != 0:
                self._record_terminal(
                    BrokerLifecycleState.REJECTED,
                    envelope,
                    broker_instance_id,
                    BrokerFailureCode.EXECUTION_FAILED,
                    context=audit_context,
                    uac_result="APPROVED",
                    handshake_result="AUTHENTICATED",
                    main_readback_result="PASSED",
                    result_integrity_result="PASSED",
                    broker_exit_code=str(broker_exit_code),
                    final_transaction_state="COMPLETED_UNVERIFIED",
                )
                return ElevatedDispatchOutcome(
                    ElevatedDispatchStatus.CLIENT_VERIFICATION_FAILED,
                    result,
                    BrokerFailureCode.EXECUTION_FAILED,
                    "The service state verified, but the Broker exit code was not successful",
                )
            final_audit_recorded = self._record_terminal(
                BrokerLifecycleState.EXITED,
                envelope,
                broker_instance_id,
                None,
                context=audit_context,
                uac_result="APPROVED",
                handshake_result="AUTHENTICATED",
                main_readback_result="PASSED",
                result_integrity_result="PASSED",
                broker_exit_code=str(broker_exit_code),
                final_transaction_state="VERIFIED_COMPLETED",
            )
            if not final_audit_recorded:
                return ElevatedDispatchOutcome(
                    ElevatedDispatchStatus.CLIENT_VERIFICATION_FAILED,
                    result,
                    BrokerFailureCode.PERSISTENCE_UNAVAILABLE,
                    "The action verified, but the mandatory Main audit could not be recorded",
                )
            return ElevatedDispatchOutcome(
                ElevatedDispatchStatus.VERIFIED,
                result,
                None,
                "Broker result and independent service readback were verified",
            )
        except (AuditUnavailableError, BrokerIpcError, BrokerTrustError, ValueError):
            self._interrupt_or_invalidate(request.request_id)
            self._record_terminal(
                BrokerLifecycleState.INTERRUPTED,
                envelope,
                broker_instance_id,
                BrokerFailureCode.IPC_DISCONNECTED,
                context=audit_context,
                uac_result="APPROVED",
                handshake_result="FAILED_OR_INTERRUPTED",
                main_readback_result="NOT_RUN",
                result_integrity_result="UNKNOWN",
                broker_exit_code="NOT_WAITED_HANDLE_CLOSED",
                final_transaction_state="INTERRUPTED",
            )
            return ElevatedDispatchOutcome(
                ElevatedDispatchStatus.INTERRUPTED,
                None,
                BrokerFailureCode.IPC_DISCONNECTED,
                "Broker communication was interrupted; the request will not be retried",
            )
        finally:
            if stream is not None:
                stream.close()
            self._launcher.close_process_handle(launch.process)

    def _verify_client_postcondition(self, envelope: PrivilegedActionEnvelope) -> bool:
        payload = envelope.request.payload
        if not isinstance(payload, (ServiceStartPayload, ServiceStopPayload)):
            return False
        current = self._platform.inspect(payload.service_identity.service_name)
        expected = (
            ServiceState.RUNNING
            if isinstance(payload, ServiceStartPayload)
            else ServiceState.STOPPED
        )
        return bool(
            current is not None
            and current.identity.canonical_digest() == envelope.request.target_identity_hash
            and current.state is expected
        )

    def _invalidate_unlaunched(self, request_id: UUID, result_code: str) -> None:
        try:
            self._repository.reject_unconsumed(
                request_id,
                result_code=result_code,
                invalidate_confirmations=True,
            )
        except (PrivilegedActionStoreError, PrivilegedReplayError):
            return

    def _interrupt_or_invalidate(self, request_id: UUID) -> None:
        try:
            if self._repository.request_is_consumed(request_id):
                self._repository.transition(
                    request_id,
                    PrivilegedTransactionState.INTERRUPTED,
                    PrivilegedReplayState.CONSUMED,
                    result_code="BROKER_CONNECTION_INTERRUPTED",
                )
            else:
                self._invalidate_unlaunched(request_id, "BROKER_CONNECTION_INTERRUPTED")
        except PrivilegedActionStoreError:
            return

    def _record_terminal(
        self,
        state: BrokerLifecycleState,
        envelope: PrivilegedActionEnvelope,
        broker_instance_id: UUID,
        failure: BrokerFailureCode | None,
        *,
        context: ElevatedBrokerAuditContext,
        uac_result: str,
        handshake_result: str,
        broker_exit_code: str,
        final_transaction_state: str,
        main_readback_result: str | None = None,
        result_integrity_result: str | None = None,
    ) -> bool:
        try:
            self._audit.lifecycle(
                state=state,
                broker_instance_id=broker_instance_id,
                request_id=envelope.request.request_id,
                plan_id=envelope.request.plan_id,
                failure=failure,
                context=context,
                uac_result=uac_result,
                handshake_result=handshake_result,
                main_readback_result=main_readback_result,
                result_integrity_result=result_integrity_result,
                broker_exit_code=broker_exit_code,
                final_transaction_state=final_transaction_state,
            )
        except AuditUnavailableError:
            return False
        return True
