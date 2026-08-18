"""Integration tests for backed-up Stage 4C2 change and restore workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from tests.stage4c1_support import FakeServicePlatform, service_observation
from tests.stage4c2_support import FakeProtector, FakeServiceStartupPlatform, configuration

from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.audit.service_startup_actions import ServiceStartupActionAuditLogger
from pc_manager_agent.confirmation.service_startup_actions import (
    ServiceStartupActionConfirmationService,
)
from pc_manager_agent.domain.service_actions import ServiceStartupType
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionType,
    ServiceStartupErrorCode,
    ServiceStartupPermissionEvidence,
    ServiceStartupTransactionState,
)
from pc_manager_agent.domain.service_startup_errors import ServiceStartupActionError
from pc_manager_agent.orchestration.service_startup_actions import ServiceStartupActionService
from pc_manager_agent.orchestration.service_target_resolver import ServiceTargetResolver
from pc_manager_agent.persistence.service_startup_actions import (
    ServiceStartupActionRepository,
    ServiceStartupBackupVault,
    ServiceStartupExecutionGuard,
    ServiceStartupStoreError,
)
from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy
from pc_manager_agent.safety.service_startup_policy import ServiceStartupSafetyPolicy
from pc_manager_agent.safety.service_startup_preview import ServiceStartupPreviewEngine
from pc_manager_agent.safety.service_startup_validator import ServiceStartupSafetyValidator
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.service_startup_actions import (
    RestoreServiceStartupTool,
    SetServiceAutomaticTool,
    SetServiceManualTool,
)


@dataclass
class Workflow:
    """Test dependency bundle with mutable fake platforms exposed for drift injection."""

    service: ServiceStartupActionService
    control: FakeServicePlatform
    startup: FakeServiceStartupPlatform
    repository: ServiceStartupActionRepository
    vault: ServiceStartupBackupVault
    audit_repository: AuditRepository

    def close(self) -> None:
        """Release every SQLite engine created by the test workflow."""
        self.vault.close()
        self.repository.close()
        self.audit_repository.close()


def _workflow(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    *,
    can_change: bool = True,
    start_type: int = 2,
    fail_after_dispatch: bool = False,
    change_runtime_state: bool = False,
) -> Workflow:
    binary = tmp_path / "vendor.exe"
    binary.write_bytes(b"signed-test-placeholder")
    observation = service_observation(binary, start_type=start_type)
    control = FakeServicePlatform(observation)
    startup = FakeServiceStartupPlatform(
        control,
        permissions=ServiceStartupPermissionEvidence(
            can_query_configuration=True,
            can_change_configuration=can_change,
            process_elevated=False,
        ),
        fail_after_dispatch=fail_after_dispatch,
        change_runtime_state=change_runtime_state,
    )
    database = tmp_path / "state.db"
    audit_repository = AuditRepository(database)
    audit_repository.initialize()
    repository = ServiceStartupActionRepository(database)
    repository.initialize()
    vault = ServiceStartupBackupVault(database, FakeProtector())
    vault.initialize()
    resolver = ServiceTargetResolver(control)
    base_policy = ServiceSafetyPolicy(
        current_username=r"DESKTOP\alice",
        agent_root=tmp_path / "agent",
        windows_directory=tmp_path / "Windows",
    )
    policy = ServiceStartupSafetyPolicy(base_policy)
    preview_engine = ServiceStartupPreviewEngine(policy)
    registry = ToolRegistry(ServiceStartupExecutionGuard(repository))
    registry.register(SetServiceAutomaticTool(startup, vault))
    registry.register(SetServiceManualTool(startup, vault))
    registry.register(RestoreServiceStartupTool(startup, vault))
    service = ServiceStartupActionService(
        control_platform=control,
        startup_platform=startup,
        resolver=resolver,
        policy=policy,
        preview_engine=preview_engine,
        validator=ServiceStartupSafetyValidator(),
        confirmation=ServiceStartupActionConfirmationService(),
        vault=vault,
        repository=repository,
        registry=registry,
        audit=ServiceStartupActionAuditLogger(
            audit_repository,
            app_version="test",
            git_commit=None,
        ),
    )
    workflow = Workflow(service, control, startup, repository, vault, audit_repository)
    request.addfinalizer(workflow.close)
    return workflow


def _execute_prepared(service: ServiceStartupActionService, prepared):  # type: ignore[no-untyped-def]
    plan, preview, review = prepared
    assert review.approved
    first = service.request_plan_confirmation(plan, preview)
    service.resolve_plan_confirmation(first.confirmation_id, True, plan, preview)
    runtime_preview, immediate = service.request_runtime_confirmation(
        first.confirmation_id,
        plan,
    )
    service.resolve_runtime_confirmation(
        immediate.confirmation_id,
        True,
        plan,
        runtime_preview,
    )
    result = service.execute(
        first.confirmation_id,
        immediate.confirmation_id,
        plan,
        runtime_preview,
    )
    return plan, result


def test_change_and_conflict_checked_restore_round_trip(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    workflow = _workflow(tmp_path, request)
    prepared = workflow.service.prepare_change(
        "set selected service to manual",
        workflow.control.observation.identity,
        ServiceStartupActionType.SET_MANUAL,
    )
    plan, result = _execute_prepared(workflow.service, prepared)
    assert result.verified
    assert result.runtime_unchanged
    assert workflow.control.observation.startup_configuration.startup_type is (
        ServiceStartupType.MANUAL
    )
    assert workflow.control.observation.state is result.before_runtime_state
    changes = workflow.service.list_changes()
    assert changes[0].backup_id == plan.backup_id

    restore_plan, restore_result = _execute_prepared(
        workflow.service,
        workflow.service.prepare_restore("restore previous startup type", plan.backup_id),
    )
    assert restore_plan.action is ServiceStartupActionType.RESTORE
    assert restore_result.verified
    assert workflow.control.observation.startup_configuration.startup_type is (
        ServiceStartupType.AUTOMATIC
    )
    assert all(item.backup_id != plan.backup_id for item in workflow.service.list_changes())


def test_external_configuration_change_causes_restore_conflict(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    workflow = _workflow(tmp_path, request)
    plan, _result = _execute_prepared(
        workflow.service,
        workflow.service.prepare_change(
            "set manual",
            workflow.control.observation.identity,
            ServiceStartupActionType.SET_MANUAL,
        ),
    )
    workflow.control.observation = workflow.control.observation.model_copy(
        update={"startup_configuration": configuration(ServiceStartupType.AUTOMATIC)}
    )
    with pytest.raises(ServiceStartupActionError) as captured:
        workflow.service.prepare_restore("restore", plan.backup_id)
    assert captured.value.code is ServiceStartupErrorCode.RESTORE_CONFLICT


def test_permission_denial_blocks_confirmation(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    workflow = _workflow(tmp_path, request, can_change=False)
    with pytest.raises(ServiceStartupActionError) as captured:
        workflow.service.prepare_change(
            "set manual",
            workflow.control.observation.identity,
            ServiceStartupActionType.SET_MANUAL,
        )
    assert captured.value.code is ServiceStartupErrorCode.PRIVILEGE_REQUIRED
    assert not workflow.startup.calls


def test_backup_failure_aborts_before_preview_or_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    workflow = _workflow(tmp_path, request)

    def fail_store(*_args: object, **_kwargs: object) -> object:
        raise ServiceStartupStoreError("simulated database failure")

    monkeypatch.setattr(workflow.service._vault, "store", fail_store)
    with pytest.raises(ServiceStartupActionError) as captured:
        workflow.service.prepare_change(
            "set manual",
            workflow.control.observation.identity,
            ServiceStartupActionType.SET_MANUAL,
        )
    assert captured.value.code is ServiceStartupErrorCode.BACKUP_UNAVAILABLE
    assert not workflow.startup.calls


def test_configuration_drift_blocks_runtime_confirmation(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    workflow = _workflow(tmp_path, request)
    plan, preview, review = workflow.service.prepare_change(
        "set manual",
        workflow.control.observation.identity,
        ServiceStartupActionType.SET_MANUAL,
    )
    assert review.approved
    first = workflow.service.request_plan_confirmation(plan, preview)
    workflow.service.resolve_plan_confirmation(first.confirmation_id, True, plan, preview)
    workflow.control.observation = workflow.control.observation.model_copy(
        update={"startup_configuration": configuration(ServiceStartupType.MANUAL)}
    )
    with pytest.raises(ServiceStartupActionError):
        workflow.service.request_runtime_confirmation(first.confirmation_id, plan)
    assert not workflow.startup.calls


def test_cancellation_before_dispatch_does_not_change_configuration(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    workflow = _workflow(tmp_path, request)
    plan, preview, review = workflow.service.prepare_change(
        "set manual",
        workflow.control.observation.identity,
        ServiceStartupActionType.SET_MANUAL,
    )
    assert review.approved
    first = workflow.service.request_plan_confirmation(plan, preview)
    workflow.service.resolve_plan_confirmation(first.confirmation_id, True, plan, preview)
    runtime_preview, immediate = workflow.service.request_runtime_confirmation(
        first.confirmation_id, plan
    )
    workflow.service.resolve_runtime_confirmation(
        immediate.confirmation_id, True, plan, runtime_preview
    )
    cancellation = CancellationToken()
    cancellation.cancel()
    result = workflow.service.execute(
        first.confirmation_id,
        immediate.confirmation_id,
        plan,
        runtime_preview,
        cancellation,
    )
    assert not result.change_dispatched
    assert not result.verified
    assert workflow.control.observation.startup_configuration.startup_type is (
        ServiceStartupType.AUTOMATIC
    )


def test_runtime_state_mutation_is_reported_as_verification_failure(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    workflow = _workflow(tmp_path, request, change_runtime_state=True)
    _plan, result = _execute_prepared(
        workflow.service,
        workflow.service.prepare_change(
            "set manual",
            workflow.control.observation.identity,
            ServiceStartupActionType.SET_MANUAL,
        ),
    )
    assert not result.verified
    assert not result.runtime_unchanged
    assert not workflow.service.list_changes()


def test_repository_marks_active_write_interrupted_without_resume(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    workflow = _workflow(tmp_path, request)
    plan, preview, review = workflow.service.prepare_change(
        "set manual",
        workflow.control.observation.identity,
        ServiceStartupActionType.SET_MANUAL,
    )
    assert review.approved
    first = workflow.service.request_plan_confirmation(plan, preview)
    workflow.service.resolve_plan_confirmation(first.confirmation_id, True, plan, preview)
    runtime_preview, immediate = workflow.service.request_runtime_confirmation(
        first.confirmation_id, plan
    )
    workflow.service.resolve_runtime_confirmation(
        immediate.confirmation_id, True, plan, runtime_preview
    )
    workflow.repository.transition(
        plan.transaction_id,
        ServiceStartupTransactionState.VALIDATING,
    )
    workflow.repository.transition(
        plan.transaction_id,
        ServiceStartupTransactionState.EXECUTING,
    )
    reopened = ServiceStartupActionRepository(tmp_path / "state.db")
    request.addfinalizer(reopened.close)
    interrupted = reopened.initialize()
    assert plan.transaction_id in interrupted
    assert reopened.get(plan.transaction_id).state.value == "INTERRUPTED"
