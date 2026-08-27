"""Finite Stage 4X3 manifest, routing, dispatch, and child-environment tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    PrivilegedActionRequest,
    PrivilegedActionType,
    PrivilegeRequirement,
    PrivilegeResolution,
    PrivilegeResolutionStatus,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.orchestration.privileged_execution_router import (
    PrivilegedExecutionRoute,
    PrivilegedExecutionRouter,
)
from pc_manager_agent.platform_support.windows.child_environment import (
    sanitized_windows_child_environment,
)
from pc_manager_agent.privileged.dispatcher import (
    FreshPrivilegedEvidence,
    PrivilegedActionDispatcher,
    PrivilegedHandlerOutcome,
)
from pc_manager_agent.privileged.manifests import build_stage4x3_manifest_registry
from pc_manager_agent.privileged.revalidation import PrivilegedRevalidationError
from pc_manager_agent.tools.manifest import CancellationToken
from tests.fixtures.privileged_actions import build_privileged_test_stack, prepare_stop


class _StopHandler:
    action_types = frozenset({PrivilegedActionType.SERVICE_STOP})

    def __init__(self, state_hash: str, safety_digest: str) -> None:
        self._state_hash = state_hash
        self._safety_digest = safety_digest
        self.calls = 0

    def require(self, request: PrivilegedActionRequest) -> FreshPrivilegedEvidence:
        return FreshPrivilegedEvidence(
            action_type=request.action_type,
            target_state_hash=self._state_hash,
            safety_digest=self._safety_digest,
            validated="typed-evidence",
        )

    def execute_and_verify(
        self,
        request: PrivilegedActionRequest,
        fresh: FreshPrivilegedEvidence,
        cancellation: CancellationToken,
        on_dispatched=None,  # type: ignore[no-untyped-def]
    ) -> PrivilegedHandlerOutcome:
        del request, fresh, cancellation
        self.calls += 1
        if on_dispatched is not None:
            on_dispatched()
        return PrivilegedHandlerOutcome(
            execution_started=True,
            execution_completed=True,
            verified=True,
            uncertain=False,
            pre_state_hash=self._state_hash,
            post_state_hash="f" * 64,
            result_code="VERIFIED",
            message="Synthetic narrow action verified",
            rollback_level=RollbackLevel.MANUAL,
        )


def _resolution(status: PrivilegeResolutionStatus) -> PrivilegeResolution:
    requirement = (
        PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED
        if status is PrivilegeResolutionStatus.REQUIRED
        else PrivilegeRequirement.STANDARD_USER
    )
    return PrivilegeResolution(
        status=status,
        requirement=requirement,
        safety_allowed=True,
        preflight_complete=True,
        reason_code="SYNTHETIC",
        explanation="Synthetic route evidence",
    )


def test_real_manifest_allowlist_is_exact_and_retains_r3() -> None:
    registry = build_stage4x3_manifest_registry()
    assert registry.actions == frozenset(
        {
            PrivilegedActionType.SERVICE_START,
            PrivilegedActionType.SERVICE_STOP,
            PrivilegedActionType.SERVICE_STARTUP_TYPE_CHANGE,
            PrivilegedActionType.SERVICE_STARTUP_TYPE_RESTORE,
            PrivilegedActionType.STARTUP_MACHINE_DISABLE,
            PrivilegedActionType.STARTUP_MACHINE_RESTORE,
            PrivilegedActionType.MSI_UNINSTALL_MACHINE,
        }
    )
    assert PrivilegedActionType.SERVICE_RESTART not in registry.actions
    for action in registry.actions:
        manifest = registry.require(action)
        assert manifest.risk_floor is RiskLevel.R3
        assert manifest.required_integrity_level == "HIGH"
        assert len(manifest.canonical_digest()) == 64


def test_execution_router_never_converts_a_block_to_elevation() -> None:
    router = PrivilegedExecutionRouter()
    assert router.route(_resolution(PrivilegeResolutionStatus.NOT_REQUIRED)).route is (
        PrivilegedExecutionRoute.STANDARD_EXECUTOR
    )
    assert router.route(_resolution(PrivilegeResolutionStatus.REQUIRED)).route is (
        PrivilegedExecutionRoute.ELEVATED_BROKER
    )
    blocked = PrivilegeResolution(
        status=PrivilegeResolutionStatus.BLOCKED,
        requirement=PrivilegeRequirement.UNKNOWN,
        safety_allowed=False,
        preflight_complete=False,
        reason_code="SAFETY_BLOCK",
        explanation="Safety policy denied the action",
    )
    assert router.route(blocked).route is PrivilegedExecutionRoute.BLOCKED


def test_child_environment_is_allowlist_based_and_removes_secrets() -> None:
    clean = sanitized_windows_child_environment(
        {
            "SystemRoot": r"C:\Windows",
            "TEMP": r"C:\Temp",
            "PATH": r"C:\untrusted",
            "OPENAI_API_KEY": "secret",
            "PC_MANAGER_BROKER_SECRET": "secret",
            "RENDEZVOUS_TOKEN": "secret",
            "IPC_NONCE": "secret",
            "PROGRAMDATA": "bad\x00value",
        }
    )
    assert clean == {"SystemRoot": r"C:\Windows", "TEMP": r"C:\Temp"}


def test_dispatcher_binds_schema_policy_manifest_and_preview(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        snapshot = stack.repository.snapshot(envelope.request)
        handler = _StopHandler(
            snapshot.preview.target_state_hash,
            snapshot.preview.safety_digest,
        )
        dispatcher = PrivilegedActionDispatcher(
            build_stage4x3_manifest_registry(),
            (handler,),
        )
        fresh = dispatcher.require(
            envelope.request,
            preview_target_state_hash=snapshot.preview.target_state_hash,
            preview_safety_digest=snapshot.preview.safety_digest,
        )
        dispatched: list[bool] = []
        outcome = dispatcher.execute_and_verify(
            envelope.request,
            fresh,
            CancellationToken(),
            lambda: dispatched.append(True),
        )
        assert outcome.verified and dispatched == [True] and handler.calls == 1

        cases = (
            (
                envelope.request.model_copy(update={"action_schema_version": 99}),
                BrokerDecision.ACTION_SCHEMA_UNSUPPORTED,
            ),
            (
                envelope.request.model_copy(update={"safety_policy_version": "stale-v1"}),
                BrokerDecision.POLICY_VERSION_MISMATCH,
            ),
            (
                envelope.request.model_copy(update={"manifest_digest": "0" * 64}),
                BrokerDecision.MANIFEST_CHANGED,
            ),
        )
        for request, decision in cases:
            with pytest.raises(PrivilegedRevalidationError) as error:
                dispatcher.require(
                    request,
                    preview_target_state_hash=snapshot.preview.target_state_hash,
                    preview_safety_digest=snapshot.preview.safety_digest,
                )
            assert error.value.decision is decision
    finally:
        stack.close()


def test_dispatcher_rejects_unregistered_action_without_fallback(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(tmp_path / "state.db")
    try:
        envelope = prepare_stop(stack)
        snapshot = stack.repository.snapshot(envelope.request)
        dispatcher = PrivilegedActionDispatcher(build_stage4x3_manifest_registry(), ())
        with pytest.raises(PrivilegedRevalidationError) as error:
            dispatcher.require(
                envelope.request,
                preview_target_state_hash=snapshot.preview.target_state_hash,
                preview_safety_digest=snapshot.preview.safety_digest,
            )
        assert error.value.decision is BrokerDecision.ACTION_NOT_ALLOWLISTED
    finally:
        stack.close()
