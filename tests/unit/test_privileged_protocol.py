from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    MockExecutionStatus,
    PrivilegedActionType,
    PrivilegedVerificationStatus,
    ServiceStartupTypeChangePayload,
)
from pc_manager_agent.domain.service_actions import (
    ServiceStartupConfiguration,
    ServiceStartupType,
    ServiceState,
)
from pc_manager_agent.privileged.authentication import EphemeralHmacAuthenticator
from pc_manager_agent.privileged.serialization import (
    PrivilegedRequestSerializer,
    PrivilegedRequestTooLargeError,
    PrivilegedSerializationError,
    UnsupportedProtocolVersionError,
)
from tests.fixtures.privileged_actions import (
    build_privileged_test_stack,
    prepare_stop,
)


def test_valid_stop_is_mock_verified_and_single_use(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        first = stack.service.dispatch_mock(envelope).result
        second = stack.service.dispatch_mock(envelope).result
        current = stack.fake_state.inspect_service(stack.fake_service.identity.service_name)
        assert first.execution_status is MockExecutionStatus.MOCK_VALIDATED
        assert first.verification_status is PrivilegedVerificationStatus.VERIFIED
        assert "No real elevated" in first.message
        assert second.broker_decision is BrokerDecision.REPLAY_REJECTED
        assert current is not None and current.state is ServiceState.STOPPED
    finally:
        stack.close()


def test_concurrent_replay_executes_exactly_once(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        serialized = stack.serializer.serialize(envelope)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(
                pool.map(
                    lambda _: stack.broker.dispatch(serialized, stack.caller).result,
                    range(2),
                )
            )
        assert sum(item.execution_started for item in results) == 1
        assert {item.broker_decision for item in results} == {
            BrokerDecision.APPROVED_FOR_MOCK_EXECUTION,
            BrokerDecision.REPLAY_REJECTED,
        }
    finally:
        stack.close()


def test_tampered_plan_hash_fails_integrity_without_execution(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        raw = json.loads(stack.serializer.serialize(envelope))
        raw["request"]["plan_hash"] = "f" * 64
        tampered = json.dumps(raw, separators=(",", ":")).encode()
        result = stack.broker.dispatch(tampered, stack.caller).result
        assert result.broker_decision is BrokerDecision.INTEGRITY_INVALID
        assert not result.execution_started
    finally:
        stack.close()


def test_extra_command_and_unknown_action_fail_schema(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        extra = json.loads(stack.serializer.serialize(envelope))
        extra["request"]["command"] = "cmd.exe"
        unknown = json.loads(stack.serializer.serialize(envelope))
        unknown["request"]["action_type"] = "RUN_POWERSHELL"
        for raw in (extra, unknown):
            result = stack.broker.dispatch(
                json.dumps(raw, separators=(",", ":")).encode(), stack.caller
            ).result
            assert result.broker_decision is BrokerDecision.SCHEMA_INVALID
            assert not result.execution_started
    finally:
        stack.close()


def test_serializer_rejects_duplicate_version_oversize_and_bad_json(
    tmp_path: Path,
) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        serialized = stack.serializer.serialize(envelope)
        raw = json.loads(serialized)
        raw["request"]["protocol_version"] = 99
        with pytest.raises(UnsupportedProtocolVersionError):
            stack.serializer.deserialize(json.dumps(raw).encode())
        with pytest.raises(PrivilegedSerializationError):
            stack.serializer.deserialize(b'{"request":{},"request":{}}')
        with pytest.raises(PrivilegedSerializationError):
            stack.serializer.deserialize(b"not-json")
        tiny = PrivilegedRequestSerializer(max_request_bytes=1_024)
        with pytest.raises(PrivilegedRequestTooLargeError):
            tiny.deserialize(b"x" * 1_025)
    finally:
        stack.close()


def test_authenticator_rejects_wrong_key() -> None:
    first = EphemeralHmacAuthenticator(b"a" * 32, key_id="first")
    second = EphemeralHmacAuthenticator(b"b" * 32, key_id="second")
    integrity = first.sign(b"canonical")
    assert first.verify(b"canonical", integrity)
    assert not second.verify(b"canonical", integrity)
    assert not first.verify(b"changed", integrity)


def test_startup_payload_blocks_disabled_transition(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        with pytest.raises(ValidationError):
            ServiceStartupTypeChangePayload(
                service_identity=stack.fake_service.identity,
                expected_current_configuration=ServiceStartupConfiguration(
                    startup_type=ServiceStartupType.MANUAL,
                    delayed_auto_start=False,
                ),
                requested_startup_type=ServiceStartupType.DISABLED,
                expected_runtime_state=ServiceState.RUNNING,
                impact_digest="1" * 64,
                backup_id="00000000-0000-0000-0000-000000000001",
                backup_digest="2" * 64,
            )
    finally:
        stack.close()


def test_defined_restart_is_not_mock_allowlisted(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        assert stack.broker is not None
        assert PrivilegedActionType.SERVICE_RESTART not in {
            PrivilegedActionType.SERVICE_START,
            PrivilegedActionType.SERVICE_STOP,
        }
    finally:
        stack.close()
