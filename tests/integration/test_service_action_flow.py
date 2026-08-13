"""Integration tests for the full Stage 4C1 Fake-SCM workflow."""

from pathlib import Path

import pytest
from tests.stage4c1_support import FakeServicePlatform, service_observation

from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.audit.service_actions import ServiceActionAuditLogger
from pc_manager_agent.confirmation.service_actions import ServiceActionConfirmationService
from pc_manager_agent.domain.service_actions import (
    ServiceActionType,
    ServiceRelation,
    ServiceState,
    ServiceTransactionState,
)
from pc_manager_agent.domain.service_errors import ServiceActionError
from pc_manager_agent.orchestration.service_action_planner import ServiceActionPlanCompiler
from pc_manager_agent.orchestration.service_actions import ServiceActionService
from pc_manager_agent.orchestration.service_dependency_analyzer import ServiceDependencyAnalyzer
from pc_manager_agent.orchestration.service_target_resolver import ServiceTargetResolver
from pc_manager_agent.persistence.service_actions import (
    ServiceActionRepository,
    ServiceExecutionGuard,
)
from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy
from pc_manager_agent.safety.service_preview import ServicePreviewEngine
from pc_manager_agent.safety.service_validator import ServiceActionSafetyValidator
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.service_actions import StartServiceTool, StopServiceTool


def _service(
    tmp_path: Path, platform: FakeServicePlatform
) -> tuple[
    ServiceActionService,
    ServiceActionRepository,
    AuditRepository,
]:
    database = tmp_path / "state.db"
    audit_repository = AuditRepository(database)
    audit_repository.initialize()
    repository = ServiceActionRepository(database)
    repository.initialize()
    registry = ToolRegistry(write_guard=ServiceExecutionGuard(repository))

    def dispatched(request: object) -> None:
        from pc_manager_agent.domain.service_actions import (
            ServiceStepRequest,
            ServiceStepType,
        )

        typed = ServiceStepRequest.model_validate(request)
        repository.mark_dispatched(
            typed.transaction_id,
            (
                ServiceTransactionState.WAITING_RUNNING
                if typed.step is ServiceStepType.START
                else ServiceTransactionState.WAITING_STOPPED
            ),
        )

    registry.register(StartServiceTool(platform, dispatched))
    registry.register(StopServiceTool(platform, dispatched))
    policy = ServiceSafetyPolicy(
        current_username=r"DESKTOP\alice",
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    dependency_analyzer = ServiceDependencyAnalyzer()
    service = ServiceActionService(
        platform,
        ServiceTargetResolver(platform),
        ServiceActionPlanCompiler(),
        policy,
        ServicePreviewEngine(policy, dependency_analyzer),
        ServiceActionSafetyValidator(registry),
        ServiceActionConfirmationService(300, 60),
        repository,
        registry,
        ServiceActionAuditLogger(
            audit_repository,
            app_version="test",
            git_commit=None,
        ),
        timeout_seconds=5,
    )
    return service, repository, audit_repository


def _approve_and_execute(
    service: ServiceActionService,
    action: ServiceActionType,
) -> object:
    plan, preview, review = service.prepare(
        f"{action.value.lower()} demo",
        "UserDemoSvc",
        action,
    )
    assert review.approved
    plan_confirmation = service.request_plan_confirmation(plan, preview)
    service.resolve_plan_confirmation(plan_confirmation.confirmation_id, True, plan, preview)
    runtime_preview, runtime_confirmation = service.request_runtime_confirmation(
        plan_confirmation.confirmation_id, plan
    )
    service.resolve_runtime_confirmation(
        runtime_confirmation.confirmation_id,
        True,
        plan,
        runtime_preview,
    )
    return service.execute(
        plan_confirmation.confirmation_id,
        runtime_confirmation.confirmation_id,
        plan,
        runtime_preview,
    )


def test_restart_runs_explicit_stop_then_start(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    platform = FakeServicePlatform(service_observation(binary))
    service, repository, audit = _service(tmp_path, platform)
    result = _approve_and_execute(service, ServiceActionType.RESTART)
    assert result.completed
    assert result.final_state is ServiceState.RUNNING
    assert [step.value for step in platform.calls] == ["STOP", "START"]
    transaction = repository.get(result.transaction_id)
    assert transaction.state is ServiceTransactionState.COMPLETED
    assert transaction.next_step_index == 2
    events = audit.list_recent(20)
    assert sum(event.event_type == "service.step_completed" for event in events) == 2
    repository.close()
    audit.close()


def test_restart_start_failure_is_partially_completed_and_stopped(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    platform = FakeServicePlatform(service_observation(binary), fail_start=True)
    service, repository, audit = _service(tmp_path, platform)
    result = _approve_and_execute(service, ServiceActionType.RESTART)
    assert result.partially_completed
    assert result.final_state is ServiceState.STOPPED
    assert platform.observation.state is ServiceState.STOPPED
    rows = audit.list_recent(20)
    assert any(event.event_type == "service.execution_failed" for event in rows)
    repository.close()
    audit.close()


def test_restart_cancelled_after_stop_remains_stopped(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    platform = FakeServicePlatform(
        service_observation(binary),
        cancel_after_stop=True,
    )
    service, repository, audit = _service(tmp_path, platform)
    result = _approve_and_execute(service, ServiceActionType.RESTART)
    assert result.partially_completed
    assert result.final_state is ServiceState.STOPPED
    assert [step.value for step in platform.calls] == ["STOP"]
    assert (
        repository.get(result.transaction_id).state is ServiceTransactionState.PARTIALLY_COMPLETED
    )
    repository.close()
    audit.close()


def test_restart_configuration_change_after_stop_blocks_start(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    platform = FakeServicePlatform(
        service_observation(binary),
        mutate_identity_after_stop=True,
    )
    service, repository, audit = _service(tmp_path, platform)
    result = _approve_and_execute(service, ServiceActionType.RESTART)
    assert result.partially_completed
    assert result.final_state is ServiceState.STOPPED
    assert [step.value for step in platform.calls] == ["STOP"]
    repository.close()
    audit.close()


def test_restart_unverified_start_reports_partial_pending_state(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    platform = FakeServicePlatform(
        service_observation(binary),
        unverified_start=True,
    )
    service, repository, audit = _service(tmp_path, platform)
    result = _approve_and_execute(service, ServiceActionType.RESTART)
    assert result.partially_completed
    assert result.final_state is ServiceState.START_PENDING
    assert not result.steps[-1].verified
    repository.close()
    audit.close()


def test_start_already_running_completes_as_verified_no_op(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    platform = FakeServicePlatform(service_observation(binary))
    service, repository, audit = _service(tmp_path, platform)
    result = _approve_and_execute(service, ServiceActionType.START)
    assert result.completed and result.no_op
    assert result.final_state is ServiceState.RUNNING
    assert platform.calls == []
    repository.close()
    audit.close()


def test_stop_failure_never_runs_a_second_control(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    platform = FakeServicePlatform(service_observation(binary), fail_stop=True)
    service, repository, audit = _service(tmp_path, platform)
    with pytest.raises(RuntimeError, match="fake stop failed"):
        _approve_and_execute(service, ServiceActionType.RESTART)
    assert [step.value for step in platform.calls] == ["STOP"]
    assert any(event.event_type == "service.execution_failed" for event in audit.list_recent(20))
    repository.close()
    audit.close()


def test_state_change_after_preview_invalidates_runtime_confirmation(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    platform = FakeServicePlatform(service_observation(binary))
    service, repository, audit = _service(tmp_path, platform)
    plan, preview, review = service.prepare("stop demo", "UserDemoSvc", ServiceActionType.STOP)
    assert review.approved
    request = service.request_plan_confirmation(plan, preview)
    service.resolve_plan_confirmation(request.confirmation_id, True, plan, preview)
    platform.observation = platform.observation.model_copy(
        update={"state": ServiceState.STOPPED, "controls_accepted": 0, "process_id": 0}
    )
    with pytest.raises(RuntimeError, match="Service state changed"):
        service.request_runtime_confirmation(request.confirmation_id, plan)
    assert platform.calls == []
    repository.close()
    audit.close()


@pytest.mark.parametrize("changed", ("configuration", "dependencies"))
def test_identity_or_dependency_change_after_preview_is_blocked(tmp_path, changed) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    platform = FakeServicePlatform(service_observation(binary))
    service, repository, audit = _service(tmp_path, platform)
    plan, preview, review = service.prepare(
        "restart demo", "UserDemoSvc", ServiceActionType.RESTART
    )
    assert review.approved
    request = service.request_plan_confirmation(plan, preview)
    service.resolve_plan_confirmation(request.confirmation_id, True, plan, preview)
    if changed == "configuration":
        platform.observation = platform.observation.model_copy(
            update={
                "identity": platform.observation.identity.model_copy(
                    update={"service_account": r"DESKTOP\bob"}
                )
            }
        )
    else:
        platform.observation = platform.observation.model_copy(
            update={
                "dependents": (
                    ServiceRelation(
                        service_name="Consumer",
                        display_name="Consumer",
                        state=ServiceState.RUNNING,
                    ),
                )
            }
        )
    with pytest.raises(ServiceActionError):
        service.request_runtime_confirmation(request.confirmation_id, plan)
    assert platform.calls == []
    repository.close()
    audit.close()


def test_repository_marks_active_transaction_interrupted_without_resume(tmp_path) -> None:
    binary = tmp_path / "demo.exe"
    binary.touch()
    platform = FakeServicePlatform(service_observation(binary))
    service, repository, audit = _service(tmp_path, platform)
    plan, preview, review = service.prepare("stop demo", "UserDemoSvc", ServiceActionType.STOP)
    assert review.approved
    request = service.request_plan_confirmation(plan, preview)
    service.resolve_plan_confirmation(request.confirmation_id, True, plan, preview)
    runtime_preview, runtime = service.request_runtime_confirmation(request.confirmation_id, plan)
    service.resolve_runtime_confirmation(runtime.confirmation_id, True, plan, runtime_preview)
    repository.transition(plan.transaction_id, ServiceTransactionState.VALIDATING)
    repository.transition(plan.transaction_id, ServiceTransactionState.EXECUTING_STOP)
    repository.close()
    reopened = ServiceActionRepository(tmp_path / "state.db")
    interrupted = reopened.initialize()
    assert interrupted == (plan.transaction_id,)
    assert reopened.get(plan.transaction_id).state is ServiceTransactionState.INTERRUPTED
    reopened.close()
    audit.close()
