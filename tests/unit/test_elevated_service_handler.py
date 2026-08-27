"""Broker-side service allow-list and fresh revalidation branch tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    PrivilegedActionType,
    PrivilegedExecutionMode,
    ServiceRestartPayload,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.service_actions import (
    ServiceActionType,
    ServiceDependencyAssessment,
    ServiceStartupType,
    ServiceState,
)
from pc_manager_agent.orchestration.service_dependency_analyzer import ServiceDependencyAnalyzer
from pc_manager_agent.privileged.revalidation import PrivilegedRevalidationError
from pc_manager_agent.privileged.service_handler import WindowsServicePrivilegedHandler
from pc_manager_agent.tools.manifest import CancellationToken
from tests.fixtures.privileged_actions import build_privileged_test_stack
from tests.stage4x2_support import prepare_real_service_action, prepare_real_stop


@pytest.mark.parametrize(
    ("change", "decision"),
    [
        ("missing", BrokerDecision.TARGET_CHANGED),
        ("identity", BrokerDecision.TARGET_CHANGED),
        ("configuration", BrokerDecision.TARGET_CHANGED),
        ("dependency", BrokerDecision.TARGET_CHANGED),
        ("state", BrokerDecision.PRECONDITION_FAILED),
        ("safety", BrokerDecision.SAFETY_BLOCKED),
        ("risk", BrokerDecision.PRIVILEGE_UNSUPPORTED),
        ("restart", BrokerDecision.ACTION_NOT_ALLOWLISTED),
    ],
)
def test_handler_rejects_every_fresh_boundary_change(
    tmp_path: Path,
    change: str,
    decision: BrokerDecision,
) -> None:
    stack = build_privileged_test_stack(
        tmp_path / f"{change}.db", execution_mode=PrivilegedExecutionMode.WINDOWS_ELEVATED
    )
    try:
        envelope, platform, policy = prepare_real_stop(stack, tmp_path)
        request = envelope.request
        if change == "missing":
            platform.observation = platform.observation.model_copy(
                update={
                    "identity": platform.observation.identity.model_copy(
                        update={"service_name": "DifferentService"}
                    )
                }
            )
        elif change == "identity":
            platform.observation = platform.observation.model_copy(
                update={
                    "identity": platform.observation.identity.model_copy(
                        update={"binary_path_fingerprint": "f" * 64}
                    )
                }
            )
        elif change == "configuration":
            platform.observation = platform.observation.model_copy(
                update={
                    "startup_configuration": platform.observation.startup_configuration.model_copy(
                        update={"startup_type": ServiceStartupType.AUTOMATIC}
                    )
                }
            )
        elif change == "dependency":
            request = request.model_copy(
                update={
                    "payload": request.payload.model_copy(
                        update={"expected_dependency_digest": "f" * 64}
                    )
                }
            )
        elif change == "state":
            platform.observation = platform.observation.model_copy(
                update={"state": ServiceState.STOPPED, "process_id": 0}
            )
        elif change == "safety":
            platform.observation = platform.observation.model_copy(
                update={"publisher": "Microsoft Corporation", "publisher_verified": True}
            )
        elif change == "risk":
            request = request.model_copy(update={"risk_level": RiskLevel.R2})
        else:
            payload = request.payload
            request = request.model_copy(
                update={
                    "action_type": PrivilegedActionType.SERVICE_RESTART,
                    "payload": ServiceRestartPayload(
                        service_identity=payload.service_identity,
                        expected_startup_configuration_digest=(
                            payload.expected_startup_configuration_digest
                        ),
                        expected_dependency_digest=payload.expected_dependency_digest,
                    ),
                }
            )
        handler = WindowsServicePrivilegedHandler(platform, policy, ServiceDependencyAnalyzer())
        with pytest.raises(PrivilegedRevalidationError) as caught:
            handler.require(request)
        assert caught.value.decision is decision
    finally:
        stack.close()


def test_handler_start_dispatch_and_verify_use_only_exact_adapter(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(
        tmp_path / "start.db", execution_mode=PrivilegedExecutionMode.WINDOWS_ELEVATED
    )
    try:
        envelope, platform, policy = prepare_real_service_action(
            stack, tmp_path, ServiceActionType.START
        )
        handler = WindowsServicePrivilegedHandler(
            platform, policy, ServiceDependencyAnalyzer(), timeout_seconds=5
        )
        validated = handler.require(envelope.request)
        result = handler.execute(envelope.request, validated, CancellationToken())
        assert result.verified
        assert platform.observation.state is ServiceState.RUNNING
        assert handler.verify(envelope.request) is not None
        platform.observation = platform.observation.model_copy(
            update={"state": ServiceState.STOPPED, "process_id": 0}
        )
        assert handler.verify(envelope.request) is None
    finally:
        stack.close()


def test_handler_blocks_dependency_policy_even_when_digest_is_unchanged(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(
        tmp_path / "dependency-policy.db",
        execution_mode=PrivilegedExecutionMode.WINDOWS_ELEVATED,
    )
    try:
        envelope, platform, policy = prepare_real_stop(stack, tmp_path)

        class _BlockedDependencies:
            def assess(self, observation: object, action: object) -> ServiceDependencyAssessment:
                del action
                return ServiceDependencyAssessment(
                    allowed=False,
                    graph_digest=observation.dependency_digest(),  # type: ignore[attr-defined]
                    explanation="synthetic cascade required",
                )

        handler = WindowsServicePrivilegedHandler(
            platform,
            policy,
            _BlockedDependencies(),  # type: ignore[arg-type]
        )
        with pytest.raises(PrivilegedRevalidationError) as caught:
            handler.require(envelope.request)
        assert caught.value.decision is BrokerDecision.PRECONDITION_FAILED
        with pytest.raises(ValueError, match="timeout"):
            WindowsServicePrivilegedHandler(
                platform, policy, ServiceDependencyAnalyzer(), timeout_seconds=1
            )
    finally:
        stack.close()
