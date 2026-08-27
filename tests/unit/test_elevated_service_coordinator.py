"""Pre-UAC trust, cancellation, expiry, and one-attempt coordinator tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.audit.elevated_broker import ElevatedBrokerAuditLogger
from pc_manager_agent.domain.elevated_broker import (
    BrokerBinaryIdentity,
    BrokerFailureCode,
    BrokerTrustMode,
    ElevatedBrokerResult,
    ElevatedBrokerResultEnvelope,
    ElevatedExecutionStatus,
    ElevatedVerificationStatus,
    SignatureStatus,
    WindowsProcessIdentity,
)
from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    PrivilegedExecutionMode,
    RequestIntegrity,
)
from pc_manager_agent.domain.service_actions import ServiceState
from pc_manager_agent.orchestration.elevated_service_actions import (
    ElevatedDispatchStatus,
    ElevatedServiceActionCoordinator,
)
from pc_manager_agent.platform_support.privileged_broker import (
    BrokerLaunchArguments,
    ElevatedProcessHandle,
    ElevationLaunchResult,
    ElevationLaunchStatus,
)
from tests.fixtures.privileged_actions import build_privileged_test_stack, prepare_stop
from tests.stage4x2_support import prepare_real_stop


def _binary(sha256: str = "2" * 64) -> BrokerBinaryIdentity:
    return BrokerBinaryIdentity(
        path_hash="1" * 64,
        file_id="volume:file",
        sha256=sha256,
        size_bytes=4096,
        product_version="0.1.0",
        signature_status=SignatureStatus.UNSIGNED,
        trusted_location=False,
    )


def _caller() -> WindowsProcessIdentity:
    return WindowsProcessIdentity(
        user_sid="S-1-5-21-1-2-3-1001",
        session_id=4,
        process_id=321,
        process_creation_time_ns=1,
        image_path_hash="3" * 64,
        image_sha256="4" * 64,
        product_version="0.1.0",
        elevated=False,
        integrity_level="MEDIUM",
    )


class _Inspector:
    def __init__(self, identity: BrokerBinaryIdentity) -> None:
        self.identity = identity
        self.calls = 0

    def inspect(self, path: Path) -> BrokerBinaryIdentity:
        del path
        self.calls += 1
        return self.identity


@dataclass
class _Launcher:
    status: ElevationLaunchStatus
    exit_code: int | None = 0
    calls: int = 0
    arguments: BrokerLaunchArguments | None = None
    closed: int = 0

    def launch(self, path: Path, arguments: BrokerLaunchArguments) -> ElevationLaunchResult:
        del path
        self.calls += 1
        self.arguments = arguments
        return ElevationLaunchResult(
            self.status,
            process=(
                ElevatedProcessHandle(999, 1)
                if self.status is ElevationLaunchStatus.STARTED
                else None
            ),
            error_code=1223 if self.status is ElevationLaunchStatus.CANCELLED else None,
        )

    def close_process_handle(self, process: object) -> None:
        del process
        self.closed += 1

    def wait_for_exit(self, process: object, *, timeout_seconds: float) -> int | None:
        del process, timeout_seconds
        return self.exit_code


def _coordinator(
    stack: object,
    inspector: _Inspector,
    launcher: _Launcher,
    now: object = None,
    *,
    pipe_factory: object | None = None,
    platform: object | None = None,
):
    return ElevatedServiceActionCoordinator(
        broker_path=Path("C:/build/pc-manager-elevated-broker.exe"),
        expected_broker_sha256="2" * 64,
        trust_mode=BrokerTrustMode.DEVELOPMENT,
        caller_identity=_caller(),
        launcher=launcher,
        binary_inspector=inspector,
        pipe_client_factory=(
            pipe_factory  # type: ignore[arg-type]
            if pipe_factory is not None
            else lambda _identifier: (_ for _ in ()).throw(
                AssertionError("cancelled/prelaunch request must not open IPC")
            )
        ),
        serializer=stack.serializer,  # type: ignore[attr-defined]
        repository=stack.repository,  # type: ignore[attr-defined]
        audit=ElevatedBrokerAuditLogger(
            stack.audit_repository,
            app_version="test",  # type: ignore[attr-defined]
        ),
        service_platform=platform or object(),  # type: ignore[arg-type]
        now=now,  # type: ignore[arg-type]
    )


class _Stream:
    def close(self) -> None:
        return


class _Connector:
    def connect(self, *, timeout_seconds: float) -> _Stream:
        del timeout_seconds
        return _Stream()


def _result(envelope: object) -> ElevatedBrokerResultEnvelope:
    request = envelope.request  # type: ignore[attr-defined]
    broker_instance_id = uuid4()
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
        result_code="VERIFIED",
        message="verified",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    return ElevatedBrokerResultEnvelope(
        broker_instance_id=broker_instance_id,
        request_id=request.request_id,
        result=result,
        result_digest=result.canonical_digest(),
        integrity=RequestIntegrity(key_id="test", authentication_code="c" * 64),
    )


def test_uac_cancel_invalidates_request_and_never_retries(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(
        tmp_path / "state.db", execution_mode=PrivilegedExecutionMode.WINDOWS_ELEVATED
    )
    try:
        envelope = prepare_stop(stack)
        launcher = _Launcher(ElevationLaunchStatus.CANCELLED)
        coordinator = _coordinator(stack, _Inspector(_binary()), launcher)
        first = coordinator.dispatch(envelope)
        second = coordinator.dispatch(envelope)
        assert first.status is ElevatedDispatchStatus.ELEVATION_CANCELLED
        assert second.status is ElevatedDispatchStatus.REJECTED
        assert launcher.calls == 1
        assert launcher.arguments is not None
        assert launcher.arguments.expected_caller_process_id == _caller().process_id
        assert launcher.arguments.agent_instance_id == envelope.request.agent_instance_id
        audit_parameters = [row.parameters for row in stack.audit_repository.list_recent(20)]
        assert any(row.get("uac_result") == "CANCELLED" for row in audit_parameters)
        assert any("caller_sid_fingerprint" in row for row in audit_parameters)
        assert all(_caller().user_sid not in str(row) for row in audit_parameters)
    finally:
        stack.close()


def test_broker_replacement_blocks_before_uac(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(
        tmp_path / "state.db", execution_mode=PrivilegedExecutionMode.WINDOWS_ELEVATED
    )
    try:
        launcher = _Launcher(ElevationLaunchStatus.CANCELLED)
        outcome = _coordinator(stack, _Inspector(_binary("f" * 64)), launcher).dispatch(
            prepare_stop(stack)
        )
        assert outcome.status is ElevatedDispatchStatus.REJECTED
        assert launcher.calls == 0
    finally:
        stack.close()


def test_request_expiring_before_uac_is_rejected_without_launch(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    stack = build_privileged_test_stack(
        tmp_path / "state.db",
        execution_mode=PrivilegedExecutionMode.WINDOWS_ELEVATED,
        now=lambda: now,
    )
    try:
        envelope = prepare_stop(stack)
        launcher = _Launcher(ElevationLaunchStatus.CANCELLED)
        coordinator = _coordinator(
            stack,
            _Inspector(_binary()),
            launcher,
            now=lambda: now + timedelta(minutes=3),
        )
        assert coordinator.dispatch(envelope).status is ElevatedDispatchStatus.REJECTED
        assert launcher.calls == 0
    finally:
        stack.close()


@pytest.mark.parametrize(
    ("readback_matches", "expected"),
    [
        (True, ElevatedDispatchStatus.VERIFIED),
        (False, ElevatedDispatchStatus.CLIENT_VERIFICATION_FAILED),
    ],
)
def test_authenticated_result_still_requires_main_fresh_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    readback_matches: bool,
    expected: ElevatedDispatchStatus,
) -> None:
    stack = build_privileged_test_stack(
        tmp_path / "state.db", execution_mode=PrivilegedExecutionMode.WINDOWS_ELEVATED
    )
    try:
        envelope, platform, _policy = prepare_real_stop(stack, tmp_path)
        result = _result(envelope)

        class _Client:
            def __init__(self, *args: object, **kwargs: object) -> None:
                del args, kwargs

            def exchange(self, *args: object, **kwargs: object) -> ElevatedBrokerResultEnvelope:
                del args, kwargs
                return result

        monkeypatch.setattr(
            "pc_manager_agent.orchestration.elevated_service_actions.ElevatedBrokerClientSession",
            _Client,
        )
        if readback_matches:
            platform.observation = platform.observation.model_copy(
                update={"state": ServiceState.STOPPED, "process_id": 0}
            )
        launcher = _Launcher(ElevationLaunchStatus.STARTED)
        coordinator = _coordinator(
            stack,
            _Inspector(_binary()),
            launcher,
            pipe_factory=lambda _identifier: _Connector(),
            platform=platform,
        )
        assert coordinator.dispatch(envelope).status is expected
        assert launcher.closed == 1
    finally:
        stack.close()


@pytest.mark.parametrize(
    ("exit_code", "expected_failure"),
    [
        (None, BrokerFailureCode.IPC_TIMED_OUT),
        (25, BrokerFailureCode.EXECUTION_FAILED),
    ],
)
def test_broker_must_exit_naturally_without_termination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int | None,
    expected_failure: BrokerFailureCode,
) -> None:
    stack = build_privileged_test_stack(
        tmp_path / "state.db", execution_mode=PrivilegedExecutionMode.WINDOWS_ELEVATED
    )
    try:
        envelope, platform, _policy = prepare_real_stop(stack, tmp_path)
        result = _result(envelope)

        class _Client:
            def __init__(self, *args: object, **kwargs: object) -> None:
                del args, kwargs

            def exchange(self, *args: object, **kwargs: object) -> ElevatedBrokerResultEnvelope:
                del args, kwargs
                return result

        monkeypatch.setattr(
            "pc_manager_agent.orchestration.elevated_service_actions.ElevatedBrokerClientSession",
            _Client,
        )
        platform.observation = platform.observation.model_copy(
            update={"state": ServiceState.STOPPED, "process_id": 0}
        )
        launcher = _Launcher(ElevationLaunchStatus.STARTED, exit_code=exit_code)
        outcome = _coordinator(
            stack,
            _Inspector(_binary()),
            launcher,
            pipe_factory=lambda _identifier: _Connector(),
            platform=platform,
        ).dispatch(envelope)
        assert outcome.status is ElevatedDispatchStatus.CLIENT_VERIFICATION_FAILED
        assert outcome.failure_code is expected_failure
        assert launcher.closed == 1
    finally:
        stack.close()
