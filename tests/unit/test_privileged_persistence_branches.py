from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import SQLAlchemyError

from pc_manager_agent.confirmation.privileged_actions import PrivilegedConfirmationState
from pc_manager_agent.domain.privileged_actions import (
    PrivilegedReplayState,
    PrivilegedTransactionState,
)
from pc_manager_agent.persistence.privileged_actions import (
    PrivilegedActionRepository,
    PrivilegedActionStoreError,
    PrivilegedConfirmationRow,
    PrivilegedReplayError,
    PrivilegedTransactionRow,
    _as_utc,
)
from tests.fixtures.privileged_actions import build_privileged_test_stack, prepare_stop


def test_repository_requires_initialization_and_known_records(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    stack = build_privileged_test_stack(database)
    envelope = prepare_stop(stack)
    stack.repository.close()
    try:
        with pytest.raises(PrivilegedActionStoreError, match="not initialized"):
            stack.repository.snapshot(envelope.request)
    finally:
        stack.audit_repository.close()

    repository = PrivilegedActionRepository(database)
    try:
        repository.initialize()
        with pytest.raises(PrivilegedActionStoreError, match="Unknown privileged confirmation"):
            repository.get_confirmation(uuid4())
        with pytest.raises(PrivilegedReplayError, match="Unknown privileged request"):
            repository.snapshot(envelope.request.model_copy(update={"request_id": uuid4()}))
        with pytest.raises(PrivilegedActionStoreError, match="Unknown privileged request"):
            repository.reject_unconsumed(uuid4(), result_code="TEST")
        with pytest.raises(PrivilegedActionStoreError, match="Unknown privileged request"):
            repository.transition(
                uuid4(),
                PrivilegedTransactionState.FAILED,
                PrivilegedReplayState.CONSUMED,
                result_code="TEST",
            )
    finally:
        repository.close()


def test_repository_rejects_duplicate_and_mismatched_plan_state(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        snapshot = stack.repository.snapshot(envelope.request)
        with pytest.raises(PrivilegedActionStoreError, match="do not match"):
            stack.repository.create(
                snapshot.plan,
                snapshot.preview.model_copy(update={"plan_id": uuid4()}),
            )
        with pytest.raises(PrivilegedActionStoreError, match="already exists"):
            stack.repository.create(snapshot.plan, snapshot.preview)
        with pytest.raises(PrivilegedActionStoreError, match="transaction state changed"):
            stack.repository.save_confirmation(
                snapshot.plan_confirmation.model_copy(update={"confirmation_id": uuid4()})
            )
        with pytest.raises(PrivilegedActionStoreError, match="transition invalid"):
            stack.repository.update_confirmation(snapshot.plan_confirmation)
        with pytest.raises(PrivilegedActionStoreError, match="Unknown privileged confirmation"):
            stack.repository.update_confirmation(
                snapshot.plan_confirmation.model_copy(update={"confirmation_id": uuid4()})
            )
        with pytest.raises(PrivilegedActionStoreError, match="Runtime privileged Preview is stale"):
            stack.repository.bind_runtime_preview(
                snapshot.plan,
                snapshot.preview.model_copy(update={"plan_id": uuid4()}),
            )
        with pytest.raises(PrivilegedActionStoreError, match="plan approval is stale"):
            stack.repository.bind_runtime_preview(snapshot.plan, snapshot.preview)
        with pytest.raises(PrivilegedActionStoreError, match="bindings are stale"):
            stack.repository.register_request(envelope)
    finally:
        stack.close()


def test_duplicate_request_identity_is_rejected_durably(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        with stack.repository._sessions.begin() as session:
            session.execute(
                update(PrivilegedTransactionRow)
                .where(PrivilegedTransactionRow.plan_id == str(envelope.request.plan_id))
                .values(state=PrivilegedTransactionState.AUTHORIZED.value)
            )
        with pytest.raises(PrivilegedReplayError, match="already used"):
            stack.repository.register_request(envelope)
    finally:
        stack.close()


def test_snapshot_and_consume_fail_when_confirmation_evidence_disappears(
    tmp_path: Path,
) -> None:
    for operation in ("snapshot", "consume"):
        stack = build_privileged_test_stack(tmp_path / f"{operation}.db")
        try:
            envelope = prepare_stop(stack)
            with stack.repository._sessions.begin() as session:
                session.execute(
                    delete(PrivilegedConfirmationRow).where(
                        PrivilegedConfirmationRow.confirmation_id
                        == str(envelope.request.confirmation_id)
                    )
                )
            if operation == "snapshot":
                with pytest.raises(PrivilegedActionStoreError, match="incomplete"):
                    stack.repository.snapshot(envelope.request)
            else:
                with pytest.raises(PrivilegedActionStoreError, match="disappeared"):
                    stack.repository.consume(envelope.request, now=datetime.now(UTC))
        finally:
            stack.close()


def test_atomic_consume_rejects_expiry_and_replay(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        with pytest.raises(PrivilegedReplayError, match="expired"):
            stack.repository.consume(envelope.request, now=envelope.request.expires_at)
        stack.repository.consume(envelope.request, now=envelope.request.created_at)
        with pytest.raises(PrivilegedReplayError, match="stale or replayed"):
            stack.repository.consume(envelope.request, now=envelope.request.created_at)
    finally:
        stack.close()


def test_reject_and_transition_never_reopen_authority(tmp_path: Path) -> None:
    rejected_stack = build_privileged_test_stack(tmp_path / "rejected.db")
    try:
        rejected = prepare_stop(rejected_stack)
        rejected_stack.repository.reject_unconsumed(
            rejected.request.request_id,
            result_code="AUTHENTIC_STALE_REQUEST",
        )
        with pytest.raises(PrivilegedReplayError, match="already terminal"):
            rejected_stack.repository.reject_unconsumed(
                rejected.request.request_id,
                result_code="SECOND_ATTEMPT",
            )
    finally:
        rejected_stack.close()

    unconsumed_stack = build_privileged_test_stack(tmp_path / "unconsumed.db")
    try:
        envelope = prepare_stop(unconsumed_stack)
        with pytest.raises(PrivilegedReplayError, match="Unconsumed"):
            unconsumed_stack.repository.transition(
                envelope.request.request_id,
                PrivilegedTransactionState.EXECUTING,
                PrivilegedReplayState.CONSUMING,
                result_code="INVALID",
            )
    finally:
        unconsumed_stack.close()


def test_restart_interrupts_active_plan_without_request_row(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    stack = build_privileged_test_stack(database)
    envelope = prepare_stop(stack)
    with stack.repository._sessions.begin() as session:
        session.execute(
            update(PrivilegedTransactionRow)
            .where(PrivilegedTransactionRow.plan_id == str(envelope.request.plan_id))
            .values(request_id=None, state=PrivilegedTransactionState.VALIDATING.value)
        )
    stack.repository.close()
    repository = PrivilegedActionRepository(database)
    try:
        interrupted = repository.initialize()
        assert envelope.request.plan_id in interrupted
    finally:
        repository.close()
        stack.audit_repository.close()


def test_as_utc_normalizes_both_sqlite_datetime_forms() -> None:
    naive = datetime(2026, 1, 1)
    offset = datetime(2026, 1, 1, 8, tzinfo=timezone(timedelta(hours=8)))
    assert _as_utc(naive).tzinfo is UTC
    assert _as_utc(offset) == datetime(2026, 1, 1, tzinfo=UTC)


class _BrokenSessions:
    def begin(self) -> NoReturn:
        raise SQLAlchemyError("synthetic database failure")

    def __call__(self) -> NoReturn:
        raise SQLAlchemyError("synthetic database failure")


def test_repository_translates_sqlalchemy_failures_fail_closed(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        snapshot = stack.repository.snapshot(envelope.request)
        stack.repository._sessions = _BrokenSessions()  # type: ignore[assignment]
        operations = (
            lambda: stack.repository.create(snapshot.plan, snapshot.preview),
            lambda: stack.repository.save_confirmation(snapshot.plan_confirmation),
            lambda: stack.repository.get_confirmation(snapshot.plan_confirmation.confirmation_id),
            lambda: stack.repository.update_confirmation(snapshot.plan_confirmation),
            lambda: stack.repository.bind_runtime_preview(snapshot.plan, snapshot.preview),
            lambda: stack.repository.register_request(envelope),
            lambda: stack.repository.snapshot(envelope.request),
            lambda: stack.repository.consume(envelope.request, now=datetime.now(UTC)),
            lambda: stack.repository.reject_unconsumed(
                envelope.request.request_id,
                result_code="TEST",
            ),
            lambda: stack.repository.transition(
                envelope.request.request_id,
                PrivilegedTransactionState.FAILED,
                PrivilegedReplayState.CONSUMED,
                result_code="TEST",
            ),
        )
        for operation in operations:
            with pytest.raises(PrivilegedActionStoreError):
                operation()
    finally:
        stack.close()


def test_repository_covers_collision_unknown_plan_and_terminal_transition(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        snapshot = stack.repository.snapshot(envelope.request)
        with stack.repository._sessions.begin() as session:
            session.execute(
                update(PrivilegedTransactionRow)
                .where(PrivilegedTransactionRow.plan_id == str(envelope.request.plan_id))
                .values(state=PrivilegedTransactionState.AWAITING_PLAN_CONFIRMATION.value)
            )
        with pytest.raises(PrivilegedActionStoreError, match="already exists"):
            stack.repository.save_confirmation(snapshot.plan_confirmation)
        with pytest.raises(PrivilegedActionStoreError, match="Unknown privileged action plan"):
            stack.repository.save_confirmation(
                snapshot.plan_confirmation.model_copy(
                    update={"confirmation_id": uuid4(), "plan_id": uuid4()}
                )
            )
        with stack.repository._sessions.begin() as session:
            session.execute(
                update(PrivilegedTransactionRow)
                .where(PrivilegedTransactionRow.plan_id == str(envelope.request.plan_id))
                .values(state=PrivilegedTransactionState.SIGNED.value)
            )
        stack.repository.consume(envelope.request, now=envelope.request.created_at)
        stack.repository.transition(
            envelope.request.request_id,
            PrivilegedTransactionState.COMPLETED,
            PrivilegedReplayState.CONSUMED,
            result_code="SYNTHETIC_COMPLETE",
        )
        terminal = stack.repository.snapshot(envelope.request)
        assert terminal.transaction_state is PrivilegedTransactionState.COMPLETED
        assert terminal.replay_state is PrivilegedReplayState.CONSUMED
    finally:
        stack.close()


def test_repository_detects_confirmation_transaction_state_races(tmp_path: Path) -> None:
    from tests.unit.test_privileged_confirmation_branches import _prepare_pending

    plan_stack = build_privileged_test_stack(tmp_path / "plan.db")
    try:
        prepared = _prepare_pending(plan_stack)
        with plan_stack.repository._sessions.begin() as session:
            session.execute(
                update(PrivilegedTransactionRow)
                .where(PrivilegedTransactionRow.plan_id == str(prepared.plan.plan_id))
                .values(state=PrivilegedTransactionState.FAILED.value)
            )
        approved = prepared.plan_confirmation.model_copy(
            update={
                "state": PrivilegedConfirmationState.APPROVED,
                "confirmed_at": datetime.now(UTC),
            }
        )
        with pytest.raises(PrivilegedActionStoreError, match="Plan confirmation state changed"):
            plan_stack.repository.update_confirmation(approved)
    finally:
        plan_stack.close()

    runtime_stack = build_privileged_test_stack(tmp_path / "runtime.db")
    try:
        prepared = _prepare_pending(runtime_stack)
        runtime_stack.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        current = runtime_stack.fake_state.inspect_service(
            runtime_stack.fake_service.identity.service_name
        )
        assert current is not None
        runtime = runtime_stack.service.prepare_runtime_confirmation(
            prepared.plan_confirmation.confirmation_id,
            prepared.plan,
            target_state_hash=current.state_digest(),
            safety_digest=current.safety_digest,
            privilege_resolution=current.privilege_resolution,
        )
        with runtime_stack.repository._sessions.begin() as session:
            session.execute(
                update(PrivilegedTransactionRow)
                .where(PrivilegedTransactionRow.plan_id == str(prepared.plan.plan_id))
                .values(state=PrivilegedTransactionState.FAILED.value)
            )
        approved = runtime.runtime_confirmation.model_copy(
            update={
                "state": PrivilegedConfirmationState.APPROVED,
                "confirmed_at": datetime.now(UTC),
            }
        )
        with pytest.raises(PrivilegedActionStoreError, match="Runtime confirmation state changed"):
            runtime_stack.repository.update_confirmation(approved)
    finally:
        runtime_stack.close()
