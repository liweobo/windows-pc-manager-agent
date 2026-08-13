"""Unit tests for Stage 4C1 models, policy, dependencies, and confirmation."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from pc_manager_agent.confirmation.service_actions import (
    ServiceActionConfirmationService,
    ServiceConfirmationError,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.service_actions import (
    ServiceActionType,
    ServicePermissionEvidence,
    ServiceRelation,
    ServiceSafetyDecision,
    ServiceState,
)
from pc_manager_agent.orchestration.service_action_planner import (
    ServiceActionPlanCompiler,
    service_action_intent,
    service_target_query,
)
from pc_manager_agent.orchestration.service_dependency_analyzer import ServiceDependencyAnalyzer
from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy
from pc_manager_agent.safety.service_preview import ServicePreviewEngine
from pc_manager_agent.safety.service_validator import ServiceActionSafetyValidator
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.service_actions import StartServiceTool, StopServiceTool
from tests.stage4c1_support import FakeServicePlatform, service_observation


def test_restart_plan_is_high_impact_and_has_explicit_steps(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    observation = service_observation(binary)
    platform = FakeServicePlatform(observation)
    plan = ServiceActionPlanCompiler().compile(
        "restart demo",
        observation.identity.service_name,
        ServiceActionType.RESTART,
        observation,
        platform.permissions,
    )
    assert plan.risk_level is RiskLevel.R2_HIGH_IMPACT
    assert [step.value for step in plan.steps] == ["STOP", "START"]


def test_dependency_analyzer_blocks_start_and_stop_without_cascade(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    dependency = ServiceRelation(
        service_name="Needed",
        display_name="Needed",
        state=ServiceState.STOPPED,
    )
    dependent = ServiceRelation(
        service_name="Consumer",
        display_name="Consumer",
        state=ServiceState.RUNNING,
    )
    analyzer = ServiceDependencyAnalyzer()
    start = analyzer.assess(
        service_observation(binary, state=ServiceState.STOPPED, dependencies=(dependency,)),
        ServiceActionType.START,
    )
    stop = analyzer.assess(
        service_observation(binary, dependents=(dependent,)),
        ServiceActionType.STOP,
    )
    assert not start.allowed and start.blocking_dependencies == (dependency,)
    assert not stop.allowed and stop.blocking_dependents == (dependent,)


def test_service_policy_blocks_system_account_and_unverified_publisher(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    policy = ServiceSafetyPolicy(
        current_username=r"DESKTOP\alice",
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    system = policy.assess(
        service_observation(binary, account="LocalSystem"),
        ServiceActionType.STOP,
    )
    unsigned = policy.assess(
        service_observation(binary, publisher_verified=False),
        ServiceActionType.STOP,
    )
    assert system.decision.value == "BLOCK"
    assert unsigned.decision.value == "BLOCK"


def test_service_policy_allows_narrow_current_user_service(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    policy = ServiceSafetyPolicy(
        current_username=r"DESKTOP\alice",
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    assessment = policy.assess(service_observation(binary), ServiceActionType.STOP)
    assert assessment.decision.value == "ALLOW"


def test_policy_category_table_is_default_deny(tmp_path) -> None:
    outside = tmp_path / "outside.exe"
    outside.touch()
    agent_root = tmp_path / "agent-root"
    agent_root.mkdir()
    agent_binary = agent_root / "self.exe"
    agent_binary.touch()
    windows_root = tmp_path / "Windows"
    windows_root.mkdir()
    windows_binary = windows_root / "component.exe"
    windows_binary.touch()
    policy = ServiceSafetyPolicy(
        current_username=r"DESKTOP\alice",
        agent_root=agent_root,
        windows_directory=windows_root,
    )
    observations = (
        service_observation(outside, service_name="pc_manager_agent helper"),
        service_observation(agent_binary),
        service_observation(outside, display_name="Endpoint protection helper"),
        service_observation(outside, display_name="Network helper"),
        service_observation(outside, display_name="Logon helper"),
        service_observation(outside, display_name="Storage helper"),
        service_observation(outside, display_name="Update helper"),
        service_observation(outside, display_name="Enterprise management helper"),
        service_observation(outside).model_copy(update={"binary_path": None}),
        service_observation(windows_binary),
        service_observation(outside, service_type=0x20),
        service_observation(outside, service_type=0x110),
        service_observation(outside, service_type=0),
        service_observation(outside, publisher="Microsoft Corporation"),
    )
    assert all(
        policy.assess(observation, ServiceActionType.STOP).decision is ServiceSafetyDecision.BLOCK
        for observation in observations
    )


def test_policy_rejects_invalid_user_and_unsupported_action_states(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    with pytest.raises(ValueError, match="username"):
        ServiceSafetyPolicy(
            current_username=" ",
            agent_root=tmp_path / "agent",
            windows_directory=tmp_path / "Windows",
        )
    policy = ServiceSafetyPolicy(
        current_username=r"DESKTOP\alice",
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    cases = (
        (service_observation(binary, state=ServiceState.PAUSED), ServiceActionType.START),
        (
            service_observation(binary, state=ServiceState.STOPPED),
            ServiceActionType.RESTART,
        ),
        (
            service_observation(binary, controls_accepted=0),
            ServiceActionType.STOP,
        ),
        (
            service_observation(binary, state=ServiceState.STOPPED, start_type=4),
            ServiceActionType.START,
        ),
    )
    assert all(
        policy.assess(observation, action).decision is ServiceSafetyDecision.BLOCK
        for observation, action in cases
    )


@pytest.mark.parametrize(
    "state",
    (
        ServiceState.START_PENDING,
        ServiceState.STOP_PENDING,
        ServiceState.CONTINUE_PENDING,
        ServiceState.PAUSE_PENDING,
    ),
)
def test_service_policy_blocks_every_pending_state(tmp_path, state) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    policy = ServiceSafetyPolicy(
        current_username=r"DESKTOP\alice",
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    assessment = policy.assess(
        service_observation(binary, state=state),
        ServiceActionType.STOP,
    )
    assert assessment.decision.value == "BLOCK"


@pytest.mark.parametrize(
    "evidence",
    (
        ServicePermissionEvidence(
            can_query=True,
            can_start=True,
            can_stop=True,
            can_enumerate_dependents=True,
            process_elevated=True,
        ),
        ServicePermissionEvidence(
            can_query=True,
            can_start=False,
            can_stop=True,
            can_enumerate_dependents=True,
            process_elevated=False,
        ),
    ),
)
def test_permission_evidence_fails_closed_for_elevation_or_missing_right(evidence) -> None:
    assert not evidence.allows(ServiceActionType.RESTART)


@pytest.mark.parametrize("changed", ("identity", "state", "dependencies", "permissions"))
def test_preview_engine_rejects_every_stale_binding(tmp_path, changed) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    observation = service_observation(binary)
    platform = FakeServicePlatform(observation)
    plan = ServiceActionPlanCompiler().compile(
        "stop demo",
        observation.identity.service_name,
        ServiceActionType.STOP,
        observation,
        platform.permissions,
    )
    candidate = observation
    permissions = platform.permissions
    if changed == "identity":
        candidate = candidate.model_copy(
            update={
                "identity": candidate.identity.model_copy(
                    update={"service_account": r"DESKTOP\bob"}
                )
            }
        )
    elif changed == "state":
        candidate = candidate.model_copy(update={"state": ServiceState.STOPPED})
    elif changed == "dependencies":
        candidate = candidate.model_copy(
            update={
                "dependencies": (
                    ServiceRelation(
                        service_name="Needed",
                        display_name="Needed",
                        state=ServiceState.RUNNING,
                    ),
                )
            }
        )
    else:
        permissions = permissions.model_copy(update={"can_stop": False})
    engine = ServicePreviewEngine(
        ServiceSafetyPolicy(
            current_username=r"DESKTOP\alice",
            agent_root=tmp_path / "agent",
            windows_directory=tmp_path / "Windows",
        ),
        ServiceDependencyAnalyzer(),
    )
    with pytest.raises(ValueError):
        engine.build(plan, candidate, permissions)


def test_service_validator_approves_valid_and_rejects_missing_or_weak_tools(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    observation = service_observation(binary)
    platform = FakeServicePlatform(observation)
    policy = ServiceSafetyPolicy(
        current_username=r"DESKTOP\alice",
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    plan = ServiceActionPlanCompiler().compile(
        "stop demo",
        observation.identity.service_name,
        ServiceActionType.STOP,
        observation,
        platform.permissions,
    )
    preview = ServicePreviewEngine(policy, ServiceDependencyAnalyzer()).build(
        plan, observation, platform.permissions
    )
    registry = ToolRegistry()
    registry.register(StartServiceTool(platform))
    registry.register(StopServiceTool(platform))
    assert ServiceActionSafetyValidator(registry).review(plan, preview).approved
    missing = ServiceActionSafetyValidator(ToolRegistry()).review(plan, preview)
    assert not missing.approved and "not registered" in missing.issues[0]

    weak_registry = ToolRegistry()
    weak_registry.register(StopServiceTool(platform, risk_level=RiskLevel.R2_HIGH_IMPACT))
    weak = ServiceActionSafetyValidator(weak_registry).review(plan, preview)
    assert not weak.approved and "weakens" in weak.issues[0]


def test_service_validator_rejects_stale_policy_dependency_and_permission(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    observation = service_observation(binary)
    platform = FakeServicePlatform(observation)
    policy = ServiceSafetyPolicy(
        current_username=r"DESKTOP\alice",
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    plan = ServiceActionPlanCompiler().compile(
        "stop demo",
        observation.identity.service_name,
        ServiceActionType.STOP,
        observation,
        platform.permissions,
    )
    preview = ServicePreviewEngine(policy, ServiceDependencyAnalyzer()).build(
        plan, observation, platform.permissions
    )
    registry = ToolRegistry()
    registry.register(StopServiceTool(platform))
    validator = ServiceActionSafetyValidator(registry)
    stale = preview.model_copy(update={"plan_id": uuid4()})
    blocked = preview.model_copy(
        update={
            "safety": preview.safety.model_copy(
                update={
                    "decision": ServiceSafetyDecision.BLOCK,
                    "explanation": "blocked by test policy",
                }
            )
        }
    )
    dependent = ServiceRelation(
        service_name="Consumer",
        display_name="Consumer",
        state=ServiceState.RUNNING,
    )
    dependency_observation = observation.model_copy(update={"dependents": (dependent,)})
    dependency_plan = ServiceActionPlanCompiler().compile(
        "stop demo",
        observation.identity.service_name,
        ServiceActionType.STOP,
        dependency_observation,
        platform.permissions,
    )
    dependency_preview = ServicePreviewEngine(policy, ServiceDependencyAnalyzer()).build(
        dependency_plan, dependency_observation, platform.permissions
    )
    denied_permissions = platform.permissions.model_copy(update={"can_stop": False})
    permission_plan = ServiceActionPlanCompiler().compile(
        "stop demo",
        observation.identity.service_name,
        ServiceActionType.STOP,
        observation,
        denied_permissions,
    )
    permission_preview = ServicePreviewEngine(policy, ServiceDependencyAnalyzer()).build(
        permission_plan, observation, denied_permissions
    )
    assert not validator.review(plan, stale).approved
    assert not validator.review(plan, blocked).approved
    assert not validator.review(dependency_plan, dependency_preview).approved
    assert not validator.review(permission_plan, permission_preview).approved


def test_service_confirmation_expires_and_cannot_be_replayed(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    observation = service_observation(binary)
    platform = FakeServicePlatform(observation)
    policy = ServiceSafetyPolicy(
        current_username=r"DESKTOP\alice",
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    plan = ServiceActionPlanCompiler().compile(
        "stop demo",
        observation.identity.service_name,
        ServiceActionType.STOP,
        observation,
        platform.permissions,
    )
    preview = ServicePreviewEngine(policy, ServiceDependencyAnalyzer()).build(
        plan, observation, platform.permissions
    )
    now = datetime.now(UTC)
    service = ServiceActionConfirmationService(30, 15, now=lambda: now)
    plan_confirmation = service.request_plan(plan, preview)
    service.resolve_plan(plan_confirmation.confirmation_id, True, plan, preview)
    runtime = service.request_runtime(plan_confirmation.confirmation_id, plan, preview)
    approved = service.resolve_runtime(runtime.confirmation_id, True, plan, preview)
    assert approved.state.value == "APPROVED"
    service.consume_runtime(runtime.confirmation_id, plan, preview)
    with pytest.raises(ServiceConfirmationError):
        service.consume_runtime(runtime.confirmation_id, plan, preview)

    clock = [now]
    expired = ServiceActionConfirmationService(30, 15, now=lambda: clock[0])
    request = expired.request_plan(plan, preview)
    clock[0] = now + timedelta(seconds=31)
    with pytest.raises(ServiceConfirmationError):
        expired.resolve_plan(request.confirmation_id, True, plan, preview)


def test_service_confirmation_rejects_invalid_lifecycle_and_binding(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    observation = service_observation(binary)
    platform = FakeServicePlatform(observation)
    policy = ServiceSafetyPolicy(
        current_username=r"DESKTOP\alice",
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    plan = ServiceActionPlanCompiler().compile(
        "stop demo",
        observation.identity.service_name,
        ServiceActionType.STOP,
        observation,
        platform.permissions,
    )
    preview = ServicePreviewEngine(policy, ServiceDependencyAnalyzer()).build(
        plan, observation, platform.permissions
    )
    with pytest.raises(ValueError, match="TTL"):
        ServiceActionConfirmationService(0, 60)
    service = ServiceActionConfirmationService()
    pending = service.request_plan(plan, preview)
    with pytest.raises(ServiceConfirmationError):
        service.request_runtime(pending.confirmation_id, plan, preview)
    service.resolve_plan(pending.confirmation_id, True, plan, preview)
    with pytest.raises(ServiceConfirmationError):
        service.resolve_plan(pending.confirmation_id, True, plan, preview)
    with pytest.raises(ServiceConfirmationError):
        service.resolve_plan(uuid4(), True, plan, preview)
    with pytest.raises(ServiceConfirmationError):
        service._get(None)

    parent = service._requests[pending.confirmation_id]
    service._requests[pending.confirmation_id] = parent.model_copy(
        update={"permission_digest": "0" * 64}
    )
    with pytest.raises(ServiceConfirmationError, match="bindings"):
        service.request_runtime(pending.confirmation_id, plan, preview)


def test_runtime_confirmation_rejects_preview_substitution_and_invalid_parent(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    observation = service_observation(binary)
    platform = FakeServicePlatform(observation)
    policy = ServiceSafetyPolicy(
        current_username=r"DESKTOP\alice",
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    plan = ServiceActionPlanCompiler().compile(
        "stop demo",
        observation.identity.service_name,
        ServiceActionType.STOP,
        observation,
        platform.permissions,
    )
    preview = ServicePreviewEngine(policy, ServiceDependencyAnalyzer()).build(
        plan, observation, platform.permissions
    )
    service = ServiceActionConfirmationService()
    parent = service.request_plan(plan, preview)
    service.resolve_plan(parent.confirmation_id, True, plan, preview)
    fresh = preview.model_copy(update={"preview_id": uuid4()})
    runtime = service.request_runtime(parent.confirmation_id, plan, fresh)
    with pytest.raises(ServiceConfirmationError, match="Preview changed"):
        service.resolve_runtime(
            runtime.confirmation_id,
            True,
            plan,
            fresh.model_copy(update={"preview_id": uuid4()}),
        )
    approved = service.resolve_runtime(runtime.confirmation_id, True, plan, fresh)
    service._requests[parent.confirmation_id] = service._requests[
        parent.confirmation_id
    ].model_copy(update={"state": "REJECTED"})
    with pytest.raises(ServiceConfirmationError, match="no longer valid"):
        service.consume_runtime(approved.confirmation_id, plan, fresh)


def test_service_chat_parser_rejects_bulk_and_extracts_one_hint() -> None:
    assert service_action_intent("请重启 Docker Service") is ServiceActionType.RESTART
    assert service_target_query("请重启 Docker Service") == "Docker"
    with pytest.raises(ValueError, match="bulk"):
        service_target_query("停止所有服务")
    with pytest.raises(ValueError, match="exactly one"):
        service_target_query("重启这个服务")
