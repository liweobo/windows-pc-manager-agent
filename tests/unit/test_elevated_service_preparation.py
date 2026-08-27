"""Translation from a Stage 4C1 permission-only block to a new Stage 4X2 plan."""

from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.domain.privileged_actions import (
    PrivilegedExecutionMode,
    PrivilegedTransactionState,
)
from pc_manager_agent.domain.service_actions import (
    ServiceActionType,
    ServicePermissionEvidence,
)
from pc_manager_agent.orchestration.elevated_service_preparation import (
    ElevatedServicePreparationService,
)
from pc_manager_agent.orchestration.privileged_actions import PrivilegedActionPreparationError
from pc_manager_agent.orchestration.service_action_planner import ServiceActionPlanCompiler
from pc_manager_agent.orchestration.service_dependency_analyzer import ServiceDependencyAnalyzer
from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy
from pc_manager_agent.safety.service_preview import ServicePreviewEngine
from tests.fixtures.privileged_actions import build_privileged_test_stack
from tests.stage4c1_support import FakeServicePlatform, service_observation


def _source(
    tmp_path: Path,
    action: ServiceActionType,
    *,
    service_name: str = "UserDemoSvc",
):
    binary = tmp_path / "vendor-service.exe"
    binary.touch()
    permissions = ServicePermissionEvidence(
        can_query=True,
        can_start=False,
        can_stop=False,
        can_enumerate_dependents=True,
        process_elevated=False,
    )
    platform = FakeServicePlatform(
        service_observation(binary, service_name=service_name), permissions=permissions
    )
    policy = ServiceSafetyPolicy(
        current_username=r"DESKTOP\alice",
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    dependencies = ServiceDependencyAnalyzer()
    plan = ServiceActionPlanCompiler().compile(
        "stop exact safe service",
        service_name,
        action,
        platform.observation,
        permissions,
    )
    preview = ServicePreviewEngine(policy, dependencies).build(
        plan, platform.observation, permissions
    )
    return platform, policy, dependencies, plan, preview


def test_permission_only_block_gets_separate_real_plan_and_two_confirmations(
    tmp_path: Path,
) -> None:
    stack = build_privileged_test_stack(
        tmp_path / "state.db", execution_mode=PrivilegedExecutionMode.WINDOWS_ELEVATED
    )
    try:
        platform, policy, dependencies, source_plan, source_preview = _source(
            tmp_path, ServiceActionType.STOP
        )
        service = ElevatedServicePreparationService(
            stack.service,
            object(),
            platform,
            policy,
            dependencies,  # type: ignore[arg-type]
        )
        prepared = service.prepare(source_plan, source_preview)
        assert (
            prepared.privileged.preview.execution_mode is PrivilegedExecutionMode.WINDOWS_ELEVATED
        )
        assert not prepared.privileged.preview.mock_only
        assert prepared.privileged.plan.source_plan_id == source_plan.plan_id

        service.approve_plan(prepared, True)
        runtime = service.prepare_runtime(prepared)
        envelope = service.approve_runtime_and_build(prepared, runtime, True)
        snapshot = stack.repository.snapshot(envelope.request)
        assert snapshot.transaction_state is PrivilegedTransactionState.SIGNED
        assert envelope.request.agent_instance_id == stack.caller.agent_instance_id
    finally:
        stack.close()


def test_restart_never_enters_stage4x2(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(
        tmp_path / "state.db", execution_mode=PrivilegedExecutionMode.WINDOWS_ELEVATED
    )
    try:
        platform, policy, dependencies, plan, preview = _source(tmp_path, ServiceActionType.RESTART)
        service = ElevatedServicePreparationService(
            stack.service,
            object(),
            platform,
            policy,
            dependencies,  # type: ignore[arg-type]
        )
        with pytest.raises(PrivilegedActionPreparationError, match="Restart"):
            service.prepare(plan, preview)
    finally:
        stack.close()


def test_protected_service_cannot_be_overridden_by_admin_route(tmp_path: Path) -> None:
    stack = build_privileged_test_stack(
        tmp_path / "state.db", execution_mode=PrivilegedExecutionMode.WINDOWS_ELEVATED
    )
    try:
        platform, policy, dependencies, plan, preview = _source(
            tmp_path, ServiceActionType.STOP, service_name="WinDefend"
        )
        service = ElevatedServicePreparationService(
            stack.service,
            object(),
            platform,
            policy,
            dependencies,  # type: ignore[arg-type]
        )
        with pytest.raises(PrivilegedActionPreparationError, match="Stage 4C1-safe"):
            service.prepare(plan, preview)
    finally:
        stack.close()
