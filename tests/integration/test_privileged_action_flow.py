from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.fixtures.privileged_actions import (
    build_privileged_test_stack,
    prepare_stop,
)

from pc_manager_agent.confirmation.privileged_actions import PrivilegedConfirmationState
from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    PrivilegedReplayState,
    PrivilegedTransactionState,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.persistence.privileged_actions import PrivilegedActionRepository


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


def test_expired_request_is_rejected_without_mock_execution(tmp_path: Path) -> None:
    clock = Clock()
    stack = build_privileged_test_stack(tmp_path / "state.db", request_ttl_seconds=15, now=clock)
    try:
        envelope = prepare_stop(stack)
        clock.value += timedelta(seconds=16)
        result = stack.service.dispatch_mock(envelope).result
        assert result.broker_decision is BrokerDecision.REQUEST_EXPIRED
        assert not result.execution_started
    finally:
        stack.close()


def test_expired_confirmation_blocks_an_existing_signed_request(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        runtime = stack.repository.get_confirmation(envelope.request.confirmation_id)
        stack.repository.update_confirmation(
            runtime.model_copy(update={"state": PrivilegedConfirmationState.EXPIRED})
        )
        result = stack.service.dispatch_mock(envelope).result
        assert result.broker_decision is BrokerDecision.CONFIRMATION_INVALID
        assert not result.execution_started
    finally:
        stack.close()


def test_target_safety_and_risk_changes_each_stop_dispatch(tmp_path: Path) -> None:
    for variant in ("target", "safety", "risk"):
        stack = build_privileged_test_stack(tmp_path / f"{variant}.db")
        try:
            envelope = prepare_stop(stack)
            current = stack.fake_service
            if variant == "target":
                current = current.model_copy(
                    update={
                        "identity": current.identity.model_copy(
                            update={"binary_path_fingerprint": "f" * 64}
                        )
                    }
                )
                expected = BrokerDecision.TARGET_CHANGED
            elif variant == "safety":
                current = current.model_copy(
                    update={"safety_allowed": False, "safety_digest": "f" * 64}
                )
                expected = BrokerDecision.SAFETY_BLOCKED
            else:
                current = current.model_copy(update={"risk_level": RiskLevel.R2})
                expected = BrokerDecision.RISK_CHANGED
            stack.fake_state.replace_service(current)
            result = stack.service.dispatch_mock(envelope).result
            assert result.broker_decision is expected
            assert not result.execution_started
        finally:
            stack.close()


def test_final_toctou_failure_consumes_request(tmp_path: Path) -> None:
    holder: dict[str, object] = {}

    def change_after_consume(_request: object) -> None:
        stack = holder["stack"]
        current = stack.fake_service  # type: ignore[attr-defined]
        stack.fake_state.replace_service(  # type: ignore[attr-defined]
            current.model_copy(update={"safety_allowed": False})
        )

    stack = build_privileged_test_stack(
        tmp_path / "state.db", before_final_revalidation=change_after_consume
    )
    holder["stack"] = stack
    try:
        envelope = prepare_stop(stack)
        first = stack.service.dispatch_mock(envelope).result
        second = stack.service.dispatch_mock(envelope).result
        assert first.broker_decision is BrokerDecision.SAFETY_BLOCKED
        assert second.broker_decision is BrokerDecision.REPLAY_REJECTED
    finally:
        stack.close()


def test_mock_execution_failure_does_not_reopen_request(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        stack.fake_state.set_action_failure(stack.fake_service.identity.service_name)
        first = stack.service.dispatch_mock(envelope).result
        second = stack.service.dispatch_mock(envelope).result
        assert first.result_code == "MOCK_OPERATION_FAILED"
        assert second.broker_decision is BrokerDecision.REPLAY_REJECTED
    finally:
        stack.close()


def test_crash_during_consumption_recovers_as_interrupted_and_consumed(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    stack = build_privileged_test_stack(database)
    envelope = prepare_stop(stack)
    stack.repository.consume(envelope.request, now=datetime.now(UTC))
    stack.repository.close()
    recovered = PrivilegedActionRepository(database)
    try:
        interrupted = recovered.initialize()
        snapshot = recovered.snapshot(envelope.request)
        assert envelope.request.plan_id in interrupted
        assert snapshot.transaction_state is PrivilegedTransactionState.INTERRUPTED
        assert snapshot.replay_state is PrivilegedReplayState.CONSUMED
    finally:
        recovered.close()
        stack.audit_repository.close()
