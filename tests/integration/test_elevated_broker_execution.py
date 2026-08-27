"""Full real-mode Broker core flow with fake SCM and no Windows elevation."""

from __future__ import annotations

import json
from pathlib import Path

from tests.fixtures.privileged_actions import build_privileged_test_stack
from tests.stage4x2_support import prepare_real_stop

from pc_manager_agent.audit.elevated_broker import ElevatedBrokerAuditLogger
from pc_manager_agent.domain.elevated_broker import (
    BrokerBinaryIdentity,
    BrokerTrustMode,
    ElevatedVerificationStatus,
    SignatureStatus,
    WindowsProcessIdentity,
)
from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    PrivilegedExecutionMode,
    PrivilegedReplayState,
    PrivilegedTransactionState,
)
from pc_manager_agent.domain.service_actions import ServiceState
from pc_manager_agent.orchestration.service_dependency_analyzer import ServiceDependencyAnalyzer
from pc_manager_agent.privileged.broker_identity import BrokerTrustPolicy
from pc_manager_agent.privileged.elevated_broker import ElevatedPrivilegedBroker
from pc_manager_agent.privileged.ipc_protocol import IpcSessionAuthenticator
from pc_manager_agent.privileged.service_handler import WindowsServicePrivilegedHandler


def _process(process_id: int, *, elevated: bool, image_sha256: str) -> WindowsProcessIdentity:
    return WindowsProcessIdentity(
        user_sid="S-1-5-21-1-2-3-1001",
        session_id=8,
        process_id=process_id,
        process_creation_time_ns=process_id,
        image_path_hash=f"{process_id % 10}" * 64,
        image_sha256=image_sha256,
        product_version="0.1.0",
        elevated=elevated,
        integrity_level="HIGH" if elevated else "MEDIUM",
    )


def test_broker_revalidates_consumes_executes_verifies_and_rejects_replay(
    tmp_path: Path,
) -> None:
    stack = build_privileged_test_stack(
        tmp_path / "state.db", execution_mode=PrivilegedExecutionMode.WINDOWS_ELEVATED
    )
    try:
        envelope, platform, policy = prepare_real_stop(stack, tmp_path)
        session = IpcSessionAuthenticator(b"s" * 32, key_id="stage4x2.test")
        canonical = stack.serializer.canonical_request_bytes(envelope.request)
        authenticated = envelope.model_copy(update={"integrity": session.sign(canonical)})
        binary = BrokerBinaryIdentity(
            path_hash="1" * 64,
            file_id="volume:file",
            sha256="2" * 64,
            size_bytes=4096,
            product_version="0.1.0",
            signature_status=SignatureStatus.UNSIGNED,
            trusted_location=False,
        )
        caller = _process(101, elevated=False, image_sha256="3" * 64)
        broker_identity = _process(202, elevated=True, image_sha256=binary.sha256)
        broker = ElevatedPrivilegedBroker(
            stack.serializer,
            stack.repository,
            WindowsServicePrivilegedHandler(platform, policy, ServiceDependencyAnalyzer()),
            ElevatedBrokerAuditLogger(stack.audit_repository, app_version="test"),
            BrokerTrustPolicy(BrokerTrustMode.DEVELOPMENT, binary.sha256),
        )

        first = broker.dispatch(
            authenticated,
            session=session,
            broker_instance_id=__import__("uuid").uuid4(),
            caller_identity=caller,
            expected_caller_identity=caller,
            broker_identity=broker_identity,
            expected_broker_binary=binary,
        )
        assert first.result.decision == BrokerDecision.APPROVED_FOR_REAL_EXECUTION.value
        assert first.result.verification_status is ElevatedVerificationStatus.VERIFIED
        assert platform.observation.state is ServiceState.STOPPED
        snapshot = stack.repository.snapshot(envelope.request)
        assert snapshot.transaction_state is PrivilegedTransactionState.COMPLETED
        assert snapshot.replay_state is PrivilegedReplayState.CONSUMED
        audit_text = json.dumps(
            [row.parameters for row in stack.audit_repository.list_recent(20)],
            sort_keys=True,
        )
        assert envelope.request.nonce not in audit_text
        assert caller.user_sid not in audit_text
        assert session.secret.hex() not in audit_text
        assert "caller_sid_fingerprint" in audit_text
        assert "broker_executable_identity" in audit_text
        assert "broker_verification_result" in audit_text
        assert '"windows_logon_id": 8' in audit_text

        replay = broker.dispatch(
            authenticated,
            session=session,
            broker_instance_id=__import__("uuid").uuid4(),
            caller_identity=caller,
            expected_caller_identity=caller,
            broker_identity=broker_identity,
            expected_broker_binary=binary,
        )
        assert replay.result.decision == BrokerDecision.REPLAY_REJECTED.value
        assert len(platform.calls) == 1
    finally:
        stack.close()
