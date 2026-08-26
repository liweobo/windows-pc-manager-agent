from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import NoReturn
from uuid import UUID, uuid4

import pytest

from pc_manager_agent.audit.privileged_actions import PrivilegedActionAuditLogger
from pc_manager_agent.audit.repository import AuditUnavailableError
from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    PrivilegedActionEnvelope,
    PrivilegedActionRequest,
    PrivilegedActionResult,
    PrivilegedActionType,
    PrivilegedTransactionState,
    PrivilegedVerificationStatus,
    PrivilegeRequirement,
    ServiceRestartPayload,
    ServiceStartPayload,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.persistence.privileged_actions import (
    PrivilegedActionStoreError,
    PrivilegedConfirmationRow,
    PrivilegedReplayError,
    PrivilegedRequestRow,
    PrivilegedTransactionRow,
)
from pc_manager_agent.privileged.registry import PrivilegedActionRegistry
from pc_manager_agent.privileged.revalidation import ServicePrivilegedRevalidator
from tests.fixtures.privileged_actions import (
    PrivilegedTestStack,
    build_privileged_test_stack,
    prepare_stop,
)


class _FailingAudit:
    def __init__(
        self,
        delegate: PrivilegedActionAuditLogger,
        event: str,
        *,
        execution_started: bool | None = None,
    ) -> None:
        self._delegate = delegate
        self._event = event
        self._execution_started = execution_started

    def malformed(self, decision: BrokerDecision) -> UUID:
        if self._event == "malformed":
            raise AuditUnavailableError("synthetic audit outage")
        return self._delegate.malformed(decision)

    def validation(
        self,
        envelope: PrivilegedActionEnvelope,
        decision: BrokerDecision,
    ) -> UUID:
        if self._event == "validation":
            raise AuditUnavailableError("synthetic audit outage")
        return self._delegate.validation(envelope, decision)

    def execution(self, envelope: PrivilegedActionEnvelope, *, started: bool) -> UUID:
        if self._event == "execution" and self._execution_started is started:
            raise AuditUnavailableError("synthetic audit outage")
        return self._delegate.execution(envelope, started=started)

    def verification(
        self,
        envelope: PrivilegedActionEnvelope,
        result: PrivilegedActionResult,
    ) -> UUID:
        if self._event == "verification":
            raise AuditUnavailableError("synthetic audit outage")
        return self._delegate.verification(envelope, result)


class _NoVerificationHandler:
    def __init__(self, delegate: ServicePrivilegedRevalidator) -> None:
        self._delegate = delegate

    def require(self, request: PrivilegedActionRequest):  # type: ignore[no-untyped-def]
        return self._delegate.require(request)

    def execute(self, request: PrivilegedActionRequest) -> bool:
        return self._delegate.execute(request)

    def verify(self, request: PrivilegedActionRequest) -> None:
        del request
        return None


class _BoundPlanProxy:
    def __init__(
        self,
        plan: object,
        *,
        risk_level: RiskLevel,
        privilege_requirement: PrivilegeRequirement,
        plan_hash: str,
    ) -> None:
        self._plan = plan
        self.risk_level = risk_level
        self.privilege_requirement = privilege_requirement
        self._plan_hash = plan_hash

    def __getattr__(self, name: str) -> object:
        return getattr(self._plan, name)

    def canonical_digest(self) -> str:
        return self._plan_hash


def _prepare_restart(stack: PrivilegedTestStack) -> PrivilegedActionEnvelope:
    current = stack.fake_state.inspect_service(stack.fake_service.identity.service_name)
    assert current is not None
    payload = ServiceRestartPayload(
        service_identity=current.identity,
        expected_startup_configuration_digest=(current.startup_configuration.canonical_digest()),
        expected_dependency_digest=current.dependency_digest,
    )
    prepared = stack.service.prepare(
        source_plan_id=uuid4(),
        source_plan_hash=hashlib.sha256(b"source-plan").hexdigest(),
        payload=payload,
        target_identity_hash=current.identity.canonical_digest(),
        object_summary="one exact synthetic service",
        target_state_hash=current.state_digest(),
        safety_digest=current.safety_digest,
        privilege_resolution=current.privilege_resolution,
    )
    stack.service.resolve_plan_confirmation(
        prepared.plan_confirmation.confirmation_id,
        True,
        prepared.plan,
        prepared.preview,
    )
    runtime = stack.service.prepare_runtime_confirmation(
        prepared.plan_confirmation.confirmation_id,
        prepared.plan,
        target_state_hash=current.state_digest(),
        safety_digest=current.safety_digest,
        privilege_resolution=current.privilege_resolution,
    )
    stack.service.resolve_runtime_confirmation(
        runtime.runtime_confirmation.confirmation_id,
        True,
        prepared.plan,
        runtime.preview,
    )
    return stack.service.build_and_register(
        prepared.plan,
        runtime.preview,
        plan_confirmation_id=prepared.plan_confirmation.confirmation_id,
        runtime_confirmation_id=runtime.runtime_confirmation.confirmation_id,
    )


def test_broker_maps_malformed_transport_decisions(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        raw = json.loads(stack.serializer.serialize(envelope))
        raw["request"]["protocol_version"] = 2
        cases = (
            (b"x" * (stack.serializer.max_request_bytes + 1), BrokerDecision.REQUEST_TOO_LARGE),
            (json.dumps(raw).encode(), BrokerDecision.UNSUPPORTED_PROTOCOL_VERSION),
            (b"not-json", BrokerDecision.SCHEMA_INVALID),
        )
        for serialized, expected in cases:
            result = stack.broker.dispatch(serialized, stack.caller).result
            assert result.broker_decision is expected
            assert result.request_id is None
    finally:
        stack.close()


def test_defined_only_restart_is_rejected_and_consumes_authority(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = _prepare_restart(stack)
        first = stack.service.dispatch_mock(envelope).result
        second = stack.service.dispatch_mock(envelope).result
        assert first.broker_decision is BrokerDecision.ACTION_NOT_ALLOWLISTED
        assert second.broker_decision is BrokerDecision.REPLAY_REJECTED
        assert not first.execution_started
    finally:
        stack.close()


def test_non_signed_transactions_are_never_dispatched(tmp_path: Path) -> None:
    for state, expected in (
        (PrivilegedTransactionState.EXPIRED, BrokerDecision.CONFIRMATION_INVALID),
        (PrivilegedTransactionState.FAILED, BrokerDecision.REPLAY_REJECTED),
    ):
        stack = build_privileged_test_stack(tmp_path / f"{state.value}.db")
        try:
            envelope = prepare_stop(stack)
            with stack.repository._sessions.begin() as session:
                transaction = session.get(
                    PrivilegedTransactionRow,
                    str(envelope.request.plan_id),
                )
                assert transaction is not None
                transaction.state = state.value
                if state is PrivilegedTransactionState.EXPIRED:
                    runtime = session.get(
                        PrivilegedConfirmationRow,
                        str(envelope.request.confirmation_id),
                    )
                    assert runtime is not None
                    runtime.state = "EXPIRED"
            result = stack.service.dispatch_mock(envelope).result
            assert result.broker_decision is expected
            assert not result.execution_started
        finally:
            stack.close()


def test_broker_detects_durable_plan_preview_and_confirmation_tampering(tmp_path: Path) -> None:
    for variant, expected in (
        ("plan", BrokerDecision.PLAN_BINDING_INVALID),
        ("preview", BrokerDecision.PREVIEW_BINDING_INVALID),
        ("confirmation", BrokerDecision.CONFIRMATION_INVALID),
        ("corrupt_model", BrokerDecision.PERSISTENCE_UNAVAILABLE),
    ):
        stack = build_privileged_test_stack(tmp_path / f"{variant}.db")
        try:
            envelope = prepare_stop(stack)
            with stack.repository._sessions.begin() as session:
                if variant == "plan":
                    row = session.get(PrivilegedRequestRow, str(envelope.request.request_id))
                    assert row is not None
                    row.request_digest = "f" * 64
                elif variant in {"preview", "corrupt_model"}:
                    row = session.get(PrivilegedTransactionRow, str(envelope.request.plan_id))
                    assert row is not None
                    raw = dict(row.preview_json if variant == "preview" else row.plan_json)
                    if variant == "preview":
                        raw["safety_digest"] = "f" * 64
                        row.preview_json = raw
                    else:
                        raw["risk_level"] = "R2"
                        row.plan_json = raw
                else:
                    row = session.get(
                        PrivilegedConfirmationRow,
                        str(envelope.request.confirmation_id),
                    )
                    assert row is not None
                    row.state = "REJECTED"
            result = stack.service.dispatch_mock(envelope).result
            assert result.broker_decision is expected
            assert not result.execution_started
        finally:
            stack.close()


def test_payload_model_mismatch_is_rejected_before_handler(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        existing = stack.broker._registry.require(PrivilegedActionType.SERVICE_STOP)
        registry = PrivilegedActionRegistry()
        registry.register(replace(existing, payload_model=ServiceStartPayload))
        stack.broker._registry = registry
        result = stack.service.dispatch_mock(envelope).result
        assert result.broker_decision is BrokerDecision.ACTION_NOT_ALLOWLISTED
        assert not result.execution_started
    finally:
        stack.close()


def test_verification_failure_is_terminal_and_truthful(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        existing = stack.broker._registry.require(PrivilegedActionType.SERVICE_STOP)
        registry = PrivilegedActionRegistry()
        registry.register(
            replace(
                existing,
                handler=_NoVerificationHandler(ServicePrivilegedRevalidator(stack.fake_state)),
            )
        )
        stack.broker._registry = registry
        result = stack.service.dispatch_mock(envelope).result
        assert result.verification_status is PrivilegedVerificationStatus.VERIFICATION_FAILED
        assert result.result_code == "VERIFICATION_FAILED"
        assert stack.service.dispatch_mock(envelope).result.broker_decision is (
            BrokerDecision.REPLAY_REJECTED
        )
    finally:
        stack.close()


@pytest.mark.parametrize(
    ("event", "execution_started"),
    (
        ("validation", None),
        ("execution", True),
        ("verification", None),
    ),
)
def test_mandatory_audit_outage_fails_closed(
    tmp_path: Path,
    event: str,
    execution_started: bool | None,
) -> None:
    stack = build_privileged_test_stack(tmp_path / f"{event}.db")
    try:
        envelope = prepare_stop(stack)
        stack.broker._audit = _FailingAudit(
            stack.broker._audit,
            event,
            execution_started=execution_started,
        )
        result = stack.service.dispatch_mock(envelope).result
        assert result.broker_decision is BrokerDecision.PERSISTENCE_UNAVAILABLE
        if event == "validation":
            assert not result.execution_started
    finally:
        stack.close()


def test_malformed_rejection_audit_outage_fails_closed(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        stack.broker._audit = _FailingAudit(stack.broker._audit, "malformed")
        result = stack.broker.dispatch(b"not-json", stack.caller).result
        assert result.broker_decision is BrokerDecision.PERSISTENCE_UNAVAILABLE
    finally:
        stack.close()


def test_final_toctou_rejection_audit_outage_fails_closed(tmp_path: Path) -> None:
    holder: dict[str, PrivilegedTestStack] = {}

    def change_after_consume(_request: PrivilegedActionRequest) -> None:
        stack = holder["stack"]
        stack.fake_state.replace_service(
            stack.fake_service.model_copy(update={"safety_allowed": False})
        )

    stack = build_privileged_test_stack(
        tmp_path / "state.db",
        before_final_revalidation=change_after_consume,
    )
    holder["stack"] = stack
    try:
        envelope = prepare_stop(stack)
        stack.broker._audit = _FailingAudit(
            stack.broker._audit,
            "execution",
            execution_started=False,
        )
        result = stack.service.dispatch_mock(envelope).result
        assert result.broker_decision is BrokerDecision.PERSISTENCE_UNAVAILABLE
        assert not result.execution_started
    finally:
        stack.close()


def test_rejection_store_and_audit_errors_do_not_grant_execution(tmp_path: Path) -> None:
    for variant, expected in (
        ("replay", BrokerDecision.REPLAY_REJECTED),
        ("store", BrokerDecision.PERSISTENCE_UNAVAILABLE),
        ("audit", BrokerDecision.PERSISTENCE_UNAVAILABLE),
    ):
        stack = build_privileged_test_stack(tmp_path / f"{variant}.db")
        try:
            envelope = _prepare_restart(stack)
            if variant == "replay":

                def reject(*_args: object, **_kwargs: object) -> NoReturn:
                    raise PrivilegedReplayError("synthetic race")

                stack.repository.reject_unconsumed = reject  # type: ignore[method-assign]
            elif variant == "store":

                def reject(*_args: object, **_kwargs: object) -> NoReturn:
                    raise PrivilegedActionStoreError("synthetic outage")

                stack.repository.reject_unconsumed = reject  # type: ignore[method-assign]
            else:
                stack.broker._audit = _FailingAudit(stack.broker._audit, "validation")
            result = stack.service.dispatch_mock(envelope).result
            assert result.broker_decision is expected
            assert not result.execution_started
        finally:
            stack.close()


def test_success_audit_contains_four_digest_only_event_types(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        result = stack.service.dispatch_mock(envelope).result
        assert result.audit_id is not None
        rows = stack.audit_repository.list_recent(20)
        event_types = {row.event_type for row in rows}
        assert {
            "MAIN_AUTHORIZATION_EVENT",
            "BROKER_VALIDATION_EVENT",
            "BROKER_EXECUTION_EVENT",
            "BROKER_VERIFICATION_EVENT",
        }.issubset(event_types)
        serialized = json.dumps([row.parameters for row in rows])
        assert envelope.request.nonce not in serialized
        assert "authentication_code" not in serialized
        assert "payload" not in serialized
    finally:
        stack.close()


@pytest.mark.parametrize(
    ("stage", "failure", "expected"),
    (
        ("inspect", "replay", BrokerDecision.REPLAY_REJECTED),
        ("consume", "replay", BrokerDecision.REPLAY_REJECTED),
        ("consume", "store", BrokerDecision.PERSISTENCE_UNAVAILABLE),
    ),
)
def test_replay_store_failures_are_mapped_without_execution(
    tmp_path: Path,
    stage: str,
    failure: str,
    expected: BrokerDecision,
) -> None:
    stack = build_privileged_test_stack(tmp_path / f"{stage}-{failure}.db")
    try:
        envelope = prepare_stop(stack)

        def fail(*_args: object, **_kwargs: object) -> NoReturn:
            if failure == "replay":
                raise PrivilegedReplayError("synthetic replay race")
            raise PrivilegedActionStoreError("synthetic persistence outage")

        if stage == "inspect":
            stack.broker._replay.inspect = fail  # type: ignore[method-assign]
        else:
            stack.broker._replay.consume = fail  # type: ignore[method-assign]
        result = stack.service.dispatch_mock(envelope).result
        assert result.broker_decision is expected
        assert not result.execution_started
    finally:
        stack.close()


def test_binding_decision_retains_risk_and_privilege_fail_closed_codes(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        snapshot = stack.repository.snapshot(envelope.request)
        manifest = stack.broker._registry.require(envelope.request.action_type)
        risk_changed = snapshot.model_copy(
            update={
                "plan": _BoundPlanProxy(
                    snapshot.plan,
                    risk_level=RiskLevel.R2,
                    privilege_requirement=snapshot.plan.privilege_requirement,
                    plan_hash=envelope.request.plan_hash,
                )
            }
        )
        privilege_changed = snapshot.model_copy(
            update={
                "plan": _BoundPlanProxy(
                    snapshot.plan,
                    risk_level=snapshot.plan.risk_level,
                    privilege_requirement=PrivilegeRequirement.STANDARD_USER,
                    plan_hash=envelope.request.plan_hash,
                )
            }
        )
        assert (
            stack.broker._binding_decision(
                envelope,
                stack.caller,
                risk_changed,
                manifest.payload_model,
                envelope.request.created_at,
            )
            is BrokerDecision.RISK_CHANGED
        )
        assert (
            stack.broker._binding_decision(
                envelope,
                stack.caller,
                privilege_changed,
                manifest.payload_model,
                envelope.request.created_at,
            )
            is BrokerDecision.PRIVILEGE_UNSUPPORTED
        )
    finally:
        stack.close()


def test_fresh_safety_and_privilege_digest_changes_have_distinct_decisions(
    tmp_path: Path,
) -> None:
    for variant, expected in (
        ("safety", BrokerDecision.SAFETY_BLOCKED),
        ("privilege", BrokerDecision.PRIVILEGE_UNSUPPORTED),
    ):
        stack = build_privileged_test_stack(tmp_path / f"{variant}.db")
        try:
            envelope = prepare_stop(stack)
            current = stack.fake_service
            if variant == "safety":
                changed = current.model_copy(update={"safety_digest": "f" * 64})
            else:
                changed = current.model_copy(
                    update={
                        "privilege_resolution": current.privilege_resolution.model_copy(
                            update={"explanation": "Fresh but changed DACL evidence"}
                        )
                    }
                )
            stack.fake_state.replace_service(changed)
            result = stack.service.dispatch_mock(envelope).result
            assert result.broker_decision is expected
            assert not result.execution_started
        finally:
            stack.close()
