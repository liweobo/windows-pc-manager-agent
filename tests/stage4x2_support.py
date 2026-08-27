"""Synthetic Stage 4X2 service capability helpers; no Windows mutation or UAC."""

from __future__ import annotations

from pathlib import Path

from pc_manager_agent.domain.privileged_actions import PrivilegedActionEnvelope
from pc_manager_agent.domain.service_actions import (
    ServiceActionType,
    ServicePermissionEvidence,
    ServiceState,
)
from pc_manager_agent.orchestration.elevated_service_preparation import (
    ElevatedServicePreparationService,
)
from pc_manager_agent.orchestration.service_action_planner import ServiceActionPlanCompiler
from pc_manager_agent.orchestration.service_dependency_analyzer import ServiceDependencyAnalyzer
from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy
from pc_manager_agent.safety.service_preview import ServicePreviewEngine
from tests.fixtures.privileged_actions import PrivilegedTestStack
from tests.stage4c1_support import FakeServicePlatform, service_observation


def prepare_real_stop(
    stack: PrivilegedTestStack,
    tmp_path: Path,
) -> tuple[PrivilegedActionEnvelope, FakeServicePlatform, ServiceSafetyPolicy]:
    """Build a fully confirmed real-mode STOP capability over an in-memory service."""
    return prepare_real_service_action(stack, tmp_path, ServiceActionType.STOP)


def prepare_real_service_action(
    stack: PrivilegedTestStack,
    tmp_path: Path,
    action: ServiceActionType,
) -> tuple[PrivilegedActionEnvelope, FakeServicePlatform, ServiceSafetyPolicy]:
    """Build a fully confirmed real-mode Start or Stop capability over fake SCM."""
    if action not in {ServiceActionType.START, ServiceActionType.STOP}:
        raise ValueError("Stage 4X2 support creates only Start or Stop capabilities")
    binary = tmp_path / "stage4x2-vendor-service.exe"
    binary.touch(exist_ok=True)
    permissions = ServicePermissionEvidence(
        can_query=True,
        can_start=False,
        can_stop=False,
        can_enumerate_dependents=True,
        process_elevated=False,
    )
    platform = FakeServicePlatform(
        service_observation(
            binary,
            state=(
                ServiceState.STOPPED if action is ServiceActionType.START else ServiceState.RUNNING
            ),
        ),
        permissions=permissions,
    )
    policy = ServiceSafetyPolicy(
        current_username=r"DESKTOP\alice",
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    dependencies = ServiceDependencyAnalyzer()
    source_plan = ServiceActionPlanCompiler().compile(
        f"{action.value.casefold()} exact safe service",
        platform.observation.identity.service_name,
        action,
        platform.observation,
        permissions,
    )
    source_preview = ServicePreviewEngine(policy, dependencies).build(
        source_plan,
        platform.observation,
        permissions,
    )
    preparation = ElevatedServicePreparationService(
        stack.service,
        object(),  # type: ignore[arg-type]
        platform,
        policy,
        dependencies,
    )
    prepared = preparation.prepare(source_plan, source_preview)
    preparation.approve_plan(prepared, True)
    runtime = preparation.prepare_runtime(prepared)
    return (
        preparation.approve_runtime_and_build(prepared, runtime, True),
        platform,
        policy,
    )
