"""In-memory full Stage 4X2 mutual-authentication exchange; no UAC or SCM writes."""

from __future__ import annotations

import queue
import threading
from datetime import UTC, datetime
from pathlib import Path

from tests.fixtures.privileged_actions import build_privileged_test_stack, prepare_stop

from pc_manager_agent.domain.elevated_broker import (
    BrokerBinaryIdentity,
    ElevatedBrokerResult,
    ElevatedBrokerResultEnvelope,
    ElevatedExecutionStatus,
    ElevatedVerificationStatus,
    ServiceControlResultEvidence,
    SignatureStatus,
    WindowsProcessIdentity,
    canonical_broker_bytes,
)
from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    PrivilegedExecutionMode,
)
from pc_manager_agent.domain.service_actions import ServiceState
from pc_manager_agent.privileged.broker_session import (
    ElevatedBrokerClientSession,
    ElevatedBrokerServerSession,
)


class _MemoryPipe:
    def __init__(
        self,
        incoming: queue.Queue[bytes],
        outgoing: queue.Queue[bytes],
        *,
        peer_process_id: int,
        peer_session_id: int,
    ) -> None:
        self._incoming = incoming
        self._outgoing = outgoing
        self._buffer = bytearray()
        self._peer_process_id = peer_process_id
        self._peer_session_id = peer_session_id

    @property
    def native_handle(self) -> int:
        return 1

    @property
    def peer_process_id(self) -> int:
        return self._peer_process_id

    @property
    def peer_session_id(self) -> int:
        return self._peer_session_id

    def read_exact(self, size: int, *, timeout_seconds: float) -> bytes:
        while len(self._buffer) < size:
            self._buffer.extend(self._incoming.get(timeout=timeout_seconds))
        value = bytes(self._buffer[:size])
        del self._buffer[:size]
        return value

    def write_all(self, data: bytes, *, timeout_seconds: float) -> None:
        del timeout_seconds
        self._outgoing.put(bytes(data))

    def close(self) -> None:
        return


class _FakeBroker:
    def dispatch(self, envelope: object, **values: object) -> ElevatedBrokerResultEnvelope:
        session = values["session"]
        broker_instance_id = values["broker_instance_id"]
        request = envelope.request  # type: ignore[attr-defined]
        result = ElevatedBrokerResult(
            request_id=request.request_id,
            broker_instance_id=broker_instance_id,
            action_type=request.action_type,
            target_identity_hash=request.target_identity_hash,
            decision=BrokerDecision.APPROVED_FOR_REAL_EXECUTION.value,
            execution_status=ElevatedExecutionStatus.COMPLETED,
            verification_status=ElevatedVerificationStatus.VERIFIED,
            execution_started=True,
            pre_state=ServiceState.RUNNING,
            post_state=ServiceState.STOPPED,
            pre_state_hash="a" * 64,
            post_state_hash="b" * 64,
            action_evidence=ServiceControlResultEvidence(
                before_state=ServiceState.RUNNING,
                after_state=ServiceState.STOPPED,
            ),
            result_code="TEST_VERIFIED",
            message="Synthetic Broker result",
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        return ElevatedBrokerResultEnvelope(
            broker_instance_id=broker_instance_id,
            request_id=request.request_id,
            result=result,
            result_digest=result.canonical_digest(),
            integrity=session.sign(canonical_broker_bytes(result.model_dump(mode="json"))),
        )


def _identity(*, process_id: int, elevated: bool) -> WindowsProcessIdentity:
    return WindowsProcessIdentity(
        user_sid="S-1-5-21-1-2-3-1001",
        session_id=9,
        process_id=process_id,
        process_creation_time_ns=process_id,
        image_path_hash=f"{process_id % 10}" * 64,
        image_sha256="e" * 64 if elevated else "d" * 64,
        product_version="0.1.0",
        elevated=elevated,
        integrity_level="HIGH" if elevated else "MEDIUM",
    )


def test_full_handshake_binds_versions_processes_agent_request_and_result(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(
        tmp_path / "state.db", execution_mode=PrivilegedExecutionMode.WINDOWS_ELEVATED
    )
    try:
        envelope = prepare_stop(stack)
        caller = _identity(process_id=101, elevated=False)
        broker_identity = _identity(process_id=202, elevated=True)
        binary = BrokerBinaryIdentity(
            path_hash="1" * 64,
            file_id="volume:file",
            sha256=broker_identity.image_sha256,
            size_bytes=4096,
            product_version="0.1.0",
            signature_status=SignatureStatus.UNSIGNED,
            trusted_location=False,
        )
        to_server: queue.Queue[bytes] = queue.Queue()
        to_client: queue.Queue[bytes] = queue.Queue()
        server_pipe = _MemoryPipe(
            to_server,
            to_client,
            peer_process_id=caller.process_id,
            peer_session_id=caller.session_id,
        )
        client_pipe = _MemoryPipe(
            to_client,
            to_server,
            peer_process_id=broker_identity.process_id,
            peer_session_id=broker_identity.session_id,
        )
        server = ElevatedBrokerServerSession(
            _FakeBroker(),  # type: ignore[arg-type]
            broker_identity,
            binary,
            lambda _stream: caller,
            message_timeout_seconds=2,
        )
        broker_instance_id = __import__("uuid").uuid4()
        failure: list[BaseException] = []

        def serve() -> None:
            try:
                server.serve(
                    server_pipe,
                    broker_instance_id=broker_instance_id,
                    expected_caller_process_id=caller.process_id,
                    expected_agent_instance_id=envelope.request.agent_instance_id,
                )
            except BaseException as exc:
                failure.append(exc)

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            result = ElevatedBrokerClientSession(
                caller,
                binary,
                broker_identity.process_id,
                stack.serializer,
                message_timeout_seconds=2,
            ).exchange(
                client_pipe,
                envelope,
                broker_instance_id=broker_instance_id,
                launch_ticket_digest="f" * 64,
                agent_instance_id=envelope.request.agent_instance_id,
            )
        except queue.Empty as exc:
            raise AssertionError(f"Broker session ended early: {failure!r}") from exc
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert not failure
        assert result.request_id == envelope.request.request_id
        assert result.result.verification_status is ElevatedVerificationStatus.VERIFIED
    finally:
        stack.close()
