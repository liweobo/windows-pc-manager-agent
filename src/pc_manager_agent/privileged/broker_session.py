"""Mutually checked Stage 4X2 handshake and one-request Broker session."""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from pc_manager_agent import __version__
from pc_manager_agent.domain.elevated_broker import (
    BrokerBinaryIdentity,
    BrokerReady,
    ClientHello,
    ClientProof,
    ElevatedBrokerResultEnvelope,
    IpcMessageType,
    SessionGrant,
    WindowsProcessIdentity,
    canonical_broker_bytes,
)
from pc_manager_agent.domain.privileged_actions import (
    PrivilegedActionEnvelope,
)
from pc_manager_agent.privileged.broker_identity import BrokerTrustError
from pc_manager_agent.privileged.elevated_broker import ElevatedPrivilegedBroker
from pc_manager_agent.privileged.ipc_protocol import (
    BrokerFrameCodec,
    BrokerIpcAuthenticationError,
    BrokerIpcProtocolError,
    IpcSessionAuthenticator,
    PipeByteStream,
    build_authenticated_frame,
    build_plain_frame,
    new_opaque_id,
    parse_payload,
    transcript_digest,
    verify_authenticated_frame,
)
from pc_manager_agent.privileged.serialization import PrivilegedRequestSerializer

_HANDSHAKE_REQUEST_ID = UUID(int=0)


class AuthenticatedPipeStream(PipeByteStream, Protocol):
    """Pipe byte stream plus operating-system peer evidence and owned close."""

    @property
    def native_handle(self) -> int:
        """Return the connected pipe handle for client token impersonation."""
        ...

    @property
    def peer_process_id(self) -> int:
        """Return the peer PID from the named-pipe filesystem."""
        ...

    @property
    def peer_session_id(self) -> int:
        """Return the peer Windows session from the named-pipe filesystem."""
        ...

    def close(self) -> None:
        """Close the connected stream."""
        ...


class ElevatedBrokerServerSession:
    """Elevated endpoint: authenticate the caller, consume one request, then exit."""

    def __init__(
        self,
        broker: ElevatedPrivilegedBroker,
        broker_identity: WindowsProcessIdentity,
        broker_binary: BrokerBinaryIdentity,
        caller_identity_reader: Callable[[AuthenticatedPipeStream], WindowsProcessIdentity],
        *,
        codec: BrokerFrameCodec | None = None,
        now: Callable[[], datetime] | None = None,
        message_timeout_seconds: float = 15.0,
        application_version: str = __version__,
    ) -> None:
        self._broker = broker
        self._broker_identity = broker_identity
        self._broker_binary = broker_binary
        self._read_caller = caller_identity_reader
        self._codec = codec or BrokerFrameCodec()
        self._now = now or (lambda: datetime.now(UTC))
        self._timeout = message_timeout_seconds
        self._version = application_version

    def serve(
        self,
        stream: AuthenticatedPipeStream,
        *,
        broker_instance_id: UUID,
        expected_caller_process_id: int,
        expected_agent_instance_id: UUID,
    ) -> ElevatedBrokerResultEnvelope:
        """Serve the fixed six-message exchange and return the sent result."""
        broker_challenge = new_opaque_id()
        ready = BrokerReady(
            broker_instance_id=broker_instance_id,
            broker_process_id=self._broker_identity.process_id,
            broker_session_id=self._broker_identity.session_id,
            broker_version=self._version,
            broker_identity_digest=self._broker_binary.canonical_digest(),
            broker_challenge=broker_challenge,
        )
        self._codec.send(
            stream,
            build_plain_frame(
                message_type=IpcMessageType.BROKER_READY,
                broker_instance_id=broker_instance_id,
                request_id=_HANDSHAKE_REQUEST_ID,
                sequence=0,
                payload=ready,
                now=self._now,
            ),
            timeout_seconds=self._timeout,
        )
        hello_frame = self._codec.receive(stream, timeout_seconds=self._timeout)
        _require_route(hello_frame, broker_instance_id, _HANDSHAKE_REQUEST_ID, 1)
        if hello_frame.message_type is not IpcMessageType.CLIENT_HELLO:
            raise BrokerIpcProtocolError("Broker expected CLIENT_HELLO")
        hello = parse_payload(hello_frame, ClientHello)
        caller_identity = self._read_caller(stream)
        if caller_identity.canonical_digest() != hello.caller_identity.canonical_digest():
            raise BrokerTrustError("ClientHello identity differs from the OS pipe client")
        if (
            caller_identity.process_id != expected_caller_process_id
            or hello.agent_instance_id != expected_agent_instance_id
            or hello.application_version != self._version
            or caller_identity.user_sid != self._broker_identity.user_sid
            or caller_identity.session_id != self._broker_identity.session_id
            or caller_identity.elevated
            or not self._broker_identity.elevated
        ):
            raise BrokerTrustError("Same-account standard-user to elevated binding failed")
        transcript = transcript_digest(ready, hello)
        session = IpcSessionAuthenticator.generate(key_id=_key_id(broker_instance_id))
        grant = SessionGrant(
            session_key=_encode_key(session.secret),
            transcript_digest=transcript,
            expires_at=self._now() + timedelta(seconds=30),
        )
        self._codec.send(
            stream,
            build_plain_frame(
                message_type=IpcMessageType.SESSION_GRANT,
                broker_instance_id=broker_instance_id,
                request_id=_HANDSHAKE_REQUEST_ID,
                sequence=2,
                payload=grant,
                now=self._now,
            ),
            timeout_seconds=self._timeout,
        )
        proof_frame = self._codec.receive(stream, timeout_seconds=self._timeout)
        _require_route(proof_frame, broker_instance_id, _HANDSHAKE_REQUEST_ID, 3)
        if proof_frame.message_type is not IpcMessageType.CLIENT_PROOF:
            raise BrokerIpcProtocolError("Broker expected CLIENT_PROOF")
        verify_authenticated_frame(proof_frame, session)
        proof = parse_payload(proof_frame, ClientProof)
        if (
            proof.transcript_digest != transcript
            or proof.proof != session.client_proof(transcript)
            or self._now() >= grant.expires_at
        ):
            raise BrokerIpcAuthenticationError("Broker client proof is invalid or expired")
        request_frame = self._codec.receive(stream, timeout_seconds=self._timeout)
        if request_frame.message_type is not IpcMessageType.REQUEST or request_frame.sequence != 4:
            raise BrokerIpcProtocolError("Broker expected one authenticated REQUEST")
        if request_frame.broker_instance_id != broker_instance_id:
            raise BrokerIpcProtocolError("Broker instance changed before request")
        verify_authenticated_frame(request_frame, session)
        envelope_value = request_frame.payload.get("envelope")
        launch_ticket_digest = request_frame.payload.get("launch_ticket_digest")
        if (
            not isinstance(envelope_value, dict)
            or launch_ticket_digest != hello.launch_ticket_digest
        ):
            raise BrokerIpcProtocolError("Broker request payload or launch binding is invalid")
        envelope = PrivilegedActionEnvelope.model_validate(envelope_value)
        if (
            request_frame.request_id != envelope.request.request_id
            or envelope.request.agent_instance_id != expected_agent_instance_id
            or envelope.request.agent_instance_id != hello.agent_instance_id
        ):
            raise BrokerIpcProtocolError("Broker frame and request IDs differ")
        result = self._broker.dispatch(
            envelope,
            session=session,
            broker_instance_id=broker_instance_id,
            caller_identity=caller_identity,
            expected_caller_identity=hello.caller_identity,
            broker_identity=self._broker_identity,
            expected_broker_binary=self._broker_binary,
        )
        self._codec.send(
            stream,
            build_authenticated_frame(
                message_type=IpcMessageType.RESULT,
                broker_instance_id=broker_instance_id,
                request_id=envelope.request.request_id,
                sequence=5,
                payload={"result_envelope": result.model_dump(mode="json")},
                authenticator=session,
                now=self._now,
            ),
            timeout_seconds=self._timeout,
        )
        return result


class ElevatedBrokerClientSession:
    """Standard-user endpoint that authenticates the launched Broker and its result."""

    def __init__(
        self,
        caller_identity: WindowsProcessIdentity,
        expected_broker_binary: BrokerBinaryIdentity,
        expected_broker_process_id: int,
        serializer: PrivilegedRequestSerializer,
        *,
        codec: BrokerFrameCodec | None = None,
        now: Callable[[], datetime] | None = None,
        message_timeout_seconds: float = 15.0,
        application_version: str = __version__,
    ) -> None:
        self._caller = caller_identity
        self._expected_binary = expected_broker_binary
        self._expected_broker_pid = expected_broker_process_id
        self._serializer = serializer
        self._codec = codec or BrokerFrameCodec()
        self._now = now or (lambda: datetime.now(UTC))
        self._timeout = message_timeout_seconds
        self._version = application_version

    def exchange(
        self,
        stream: AuthenticatedPipeStream,
        envelope: PrivilegedActionEnvelope,
        *,
        broker_instance_id: UUID,
        launch_ticket_digest: str,
        agent_instance_id: UUID,
    ) -> ElevatedBrokerResultEnvelope:
        """Authenticate one launched Broker, send one request, and verify one result."""
        if (
            stream.peer_process_id != self._expected_broker_pid
            or stream.peer_session_id != self._caller.session_id
        ):
            raise BrokerTrustError("Connected pipe server is not the launched Broker process")
        ready_frame = self._codec.receive(stream, timeout_seconds=self._timeout)
        _require_route(ready_frame, broker_instance_id, _HANDSHAKE_REQUEST_ID, 0)
        if ready_frame.message_type is not IpcMessageType.BROKER_READY:
            raise BrokerIpcProtocolError("Main Agent expected BROKER_READY")
        ready = parse_payload(ready_frame, BrokerReady)
        if (
            ready.broker_process_id != self._expected_broker_pid
            or ready.broker_session_id != self._caller.session_id
            or ready.broker_version != self._version
            or ready.broker_identity_digest != self._expected_binary.canonical_digest()
        ):
            raise BrokerTrustError("Broker Ready identity differs from pre-UAC evidence")
        hello = ClientHello(
            agent_instance_id=agent_instance_id,
            application_version=self._version,
            caller_identity=self._caller,
            launch_ticket_digest=launch_ticket_digest,
            client_challenge=new_opaque_id(),
        )
        self._codec.send(
            stream,
            build_plain_frame(
                message_type=IpcMessageType.CLIENT_HELLO,
                broker_instance_id=broker_instance_id,
                request_id=_HANDSHAKE_REQUEST_ID,
                sequence=1,
                payload=hello,
                now=self._now,
            ),
            timeout_seconds=self._timeout,
        )
        grant_frame = self._codec.receive(stream, timeout_seconds=self._timeout)
        _require_route(grant_frame, broker_instance_id, _HANDSHAKE_REQUEST_ID, 2)
        if grant_frame.message_type is not IpcMessageType.SESSION_GRANT:
            raise BrokerIpcProtocolError("Main Agent expected SESSION_GRANT")
        grant = parse_payload(grant_frame, SessionGrant)
        transcript = transcript_digest(ready, hello)
        if grant.transcript_digest != transcript or self._now() >= grant.expires_at:
            raise BrokerIpcAuthenticationError("Broker session grant is stale or mismatched")
        session = IpcSessionAuthenticator(
            _decode_key(grant.session_key),
            key_id=_key_id(broker_instance_id),
        )
        proof = ClientProof(
            transcript_digest=transcript,
            proof=session.client_proof(transcript),
        )
        self._codec.send(
            stream,
            build_authenticated_frame(
                message_type=IpcMessageType.CLIENT_PROOF,
                broker_instance_id=broker_instance_id,
                request_id=_HANDSHAKE_REQUEST_ID,
                sequence=3,
                payload=proof,
                authenticator=session,
                now=self._now,
            ),
            timeout_seconds=self._timeout,
        )
        canonical = self._serializer.canonical_request_bytes(envelope.request)
        session_envelope = envelope.model_copy(update={"integrity": session.sign(canonical)})
        self._codec.send(
            stream,
            build_authenticated_frame(
                message_type=IpcMessageType.REQUEST,
                broker_instance_id=broker_instance_id,
                request_id=envelope.request.request_id,
                sequence=4,
                payload={
                    "envelope": session_envelope.model_dump(mode="json"),
                    "launch_ticket_digest": launch_ticket_digest,
                },
                authenticator=session,
                now=self._now,
            ),
            timeout_seconds=self._timeout,
        )
        result_frame = self._codec.receive(stream, timeout_seconds=self._timeout)
        _require_route(
            result_frame,
            broker_instance_id,
            envelope.request.request_id,
            5,
        )
        if result_frame.message_type is not IpcMessageType.RESULT:
            raise BrokerIpcProtocolError("Main Agent expected one RESULT")
        verify_authenticated_frame(result_frame, session)
        raw_result = result_frame.payload.get("result_envelope")
        if not isinstance(raw_result, dict):
            raise BrokerIpcProtocolError("Broker result envelope is absent")
        result = ElevatedBrokerResultEnvelope.model_validate(raw_result)
        if (
            result.request_id != envelope.request.request_id
            or result.broker_instance_id != broker_instance_id
            or not session.verify(
                canonical_broker_bytes(result.result.model_dump(mode="json")),
                result.integrity,
            )
        ):
            raise BrokerIpcAuthenticationError("Broker result authentication failed")
        return result


def _require_route(
    frame: object,
    broker_instance_id: UUID,
    request_id: UUID,
    sequence: int,
) -> None:
    from pc_manager_agent.domain.elevated_broker import IpcFrame

    if not isinstance(frame, IpcFrame) or (
        frame.broker_instance_id != broker_instance_id
        or frame.request_id != request_id
        or frame.sequence != sequence
    ):
        raise BrokerIpcProtocolError("Broker IPC routing fields changed")


def _key_id(broker_instance_id: UUID) -> str:
    return f"stage4x2.{broker_instance_id.hex}"


def _encode_key(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_key(value: str) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value + "=")
    except (ValueError, UnicodeError) as exc:
        raise BrokerIpcAuthenticationError("Broker session key encoding is invalid") from exc
    if len(decoded) != 32:
        raise BrokerIpcAuthenticationError("Broker session key length is invalid")
    return decoded
