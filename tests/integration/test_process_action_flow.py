from __future__ import annotations

from pathlib import Path

import pytest
from tests.stage4a_support import FakeProcessPlatform, process_observation

from pc_manager_agent.audit.process_actions import ProcessActionAuditLogger
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.process_actions import ProcessActionConfirmationService
from pc_manager_agent.domain.process_actions import (
    ProcessActionState,
    ProcessActionType,
    ProcessMemberResultState,
    ProcessTargetQuery,
    ProcessTargetQueryType,
)
from pc_manager_agent.orchestration.process_action_planner import ProcessActionPlanCompiler
from pc_manager_agent.orchestration.process_actions import ProcessActionService
from pc_manager_agent.orchestration.process_target_resolver import ProcessTargetResolver
from pc_manager_agent.persistence.process_actions import (
    ProcessActionRepository,
    ProcessExecutionGuard,
)
from pc_manager_agent.safety.process_policy import ProcessSafetyPolicy
from pc_manager_agent.safety.process_preview import ProcessPreviewEngine
from pc_manager_agent.safety.process_validator import ProcessActionSafetyValidator
from pc_manager_agent.tools.registry import ToolRegistry, WriteAuthorizationError
from pc_manager_agent.tools.system_tools.process_actions import (
    ForceTerminateProcessTool,
    RequestProcessExitTool,
)


def _service(
    tmp_path: Path,
    platform: FakeProcessPlatform,
) -> tuple[ProcessActionService, ProcessActionRepository, AuditRepository, ToolRegistry]:
    audit_repository = AuditRepository(tmp_path / "audit.db")
    audit_repository.initialize()
    repository = ProcessActionRepository(tmp_path / "actions.db")
    repository.initialize()
    resolver = ProcessTargetResolver(platform)
    registry = ToolRegistry(write_guard=ProcessExecutionGuard(repository))
    registry.register(RequestProcessExitTool(platform))
    registry.register(ForceTerminateProcessTool(platform))
    service = ProcessActionService(
        ProcessActionPlanCompiler(resolver),
        resolver,
        ProcessPreviewEngine(
            ProcessSafetyPolicy(
                current_owner_sid="S-1-5-21-1000",
                current_session_id=1,
                windows_directory=Path("C:/Windows"),
            )
        ),
        ProcessActionSafetyValidator(registry),
        ProcessActionConfirmationService(),
        repository,
        registry,
        ProcessActionAuditLogger(audit_repository, app_version="test", git_commit=None),
        graceful_timeout_seconds=1,
        force_timeout_seconds=1,
    )
    return service, repository, audit_repository, registry


def _execute(service: ProcessActionService, plan: object, preview: object):
    from pc_manager_agent.domain.process_actions import ProcessActionPlan, ProcessActionPreview

    typed_plan = ProcessActionPlan.model_validate(plan)
    typed_preview = ProcessActionPreview.model_validate(preview)
    first = service.request_plan_confirmation(typed_plan, typed_preview)
    service.resolve_plan_confirmation(first.confirmation_id, True, typed_plan, typed_preview)
    live, second = service.request_runtime_confirmation(first.confirmation_id, typed_plan)
    service.resolve_runtime_confirmation(second.confirmation_id, True, typed_plan, live)
    return service.execute(first.confirmation_id, second.confirmation_id, typed_plan, live)


def test_graceful_flow_requires_both_confirmations_and_audits_result(tmp_path: Path) -> None:
    platform = FakeProcessPlatform((process_observation(),))
    service, repository, audit, _registry = _service(tmp_path, platform)
    try:
        plan, preview, review = service.prepare(
            "close selected demo",
            ProcessTargetQuery(
                query_type=ProcessTargetQueryType.SELECTED_PROCESS,
                pid=4_001,
                include_application_group=False,
            ),
            ProcessActionType.REQUEST_GRACEFUL_EXIT,
        )
        assert review.approved
        result = _execute(service, plan, preview)
        assert result.all_exited
        assert platform.graceful_calls == [4_001]
        assert repository.get(plan.transaction_id).state is ProcessActionState.GRACEFUL_COMPLETED
        event_types = {row.event_type for row in audit.list_recent(20)}
        assert {
            "process.previewed",
            "process.plan_confirmation_resolved",
            "process.runtime_confirmation_resolved",
            "process.started",
            "process.completed",
        } <= event_types
    finally:
        repository.close()
        audit.close()


@pytest.mark.security
def test_registry_cannot_execute_process_tool_without_exact_capability(tmp_path: Path) -> None:
    platform = FakeProcessPlatform((process_observation(),))
    service, repository, audit, registry = _service(tmp_path, platform)
    try:
        plan, _preview, _review = service.prepare(
            "close demo",
            ProcessTargetQuery(query_type=ProcessTargetQueryType.PID, pid=4_001),
            ProcessActionType.REQUEST_GRACEFUL_EXIT,
        )
        with pytest.raises(WriteAuthorizationError):
            registry.execute("system.process.request_exit", service._arguments(plan))
        assert platform.graceful_calls == []
    finally:
        repository.close()
        audit.close()


def test_identity_change_after_confirmation_blocks_without_platform_call(tmp_path: Path) -> None:
    original = process_observation()
    platform = FakeProcessPlatform((original,))
    service, repository, audit, _registry = _service(tmp_path, platform)
    try:
        plan, preview, _review = service.prepare(
            "close demo",
            ProcessTargetQuery(
                query_type=ProcessTargetQueryType.PID,
                pid=4_001,
                include_application_group=False,
            ),
            ProcessActionType.REQUEST_GRACEFUL_EXIT,
        )
        first = service.request_plan_confirmation(plan, preview)
        service.resolve_plan_confirmation(first.confirmation_id, True, plan, preview)
        live, second = service.request_runtime_confirmation(first.confirmation_id, plan)
        service.resolve_runtime_confirmation(second.confirmation_id, True, plan, live)
        platform.observations = (process_observation(create_second=2),)
        with pytest.raises(Exception, match="different process"):
            service.execute(first.confirmation_id, second.confirmation_id, plan, live)
        assert platform.graceful_calls == []
        assert repository.get(plan.transaction_id).state is ProcessActionState.BLOCKED
    finally:
        repository.close()
        audit.close()


def test_new_safety_block_before_runtime_confirmation_is_persisted_and_audited(
    tmp_path: Path,
) -> None:
    original = process_observation()
    platform = FakeProcessPlatform((original,))
    service, repository, audit, _registry = _service(tmp_path, platform)
    try:
        plan, preview, _review = service.prepare(
            "close demo",
            ProcessTargetQuery(
                query_type=ProcessTargetQueryType.PID,
                pid=4_001,
                include_application_group=False,
            ),
            ProcessActionType.REQUEST_GRACEFUL_EXIT,
        )
        first = service.request_plan_confirmation(plan, preview)
        service.resolve_plan_confirmation(first.confirmation_id, True, plan, preview)
        platform.observations = (process_observation(service_names=("DemoService",)),)

        with pytest.raises(Exception, match="blocked by safety policy"):
            service.request_runtime_confirmation(first.confirmation_id, plan)

        assert platform.graceful_calls == []
        assert repository.get(plan.transaction_id).state is ProcessActionState.BLOCKED
        failed = next(
            row for row in audit.list_recent(10) if row.event_type == "process.execution_failed"
        )
        assert failed.parameters["phase"] == "runtime_confirmation_safety_review"
        assert failed.error["mutation_may_have_started"] is False
    finally:
        repository.close()
        audit.close()


def test_graceful_timeout_requires_new_force_transaction_and_confirmations(
    tmp_path: Path,
) -> None:
    platform = FakeProcessPlatform((process_observation(),))
    platform.graceful_state = ProcessMemberResultState.STILL_RUNNING
    service, repository, audit, _registry = _service(tmp_path, platform)
    try:
        graceful, preview, _review = service.prepare(
            "close demo",
            ProcessTargetQuery(
                query_type=ProcessTargetQueryType.PID,
                pid=4_001,
                include_application_group=False,
            ),
            ProcessActionType.REQUEST_GRACEFUL_EXIT,
        )
        result = _execute(service, graceful, preview)
        assert result.members[0].state is ProcessMemberResultState.STILL_RUNNING
        assert repository.get(graceful.transaction_id).state is ProcessActionState.GRACEFUL_TIMEOUT

        force, force_preview, review = service.prepare_force_after_graceful(graceful)
        assert review.approved
        assert force.action is ProcessActionType.FORCE_TERMINATE
        assert force.parent_transaction_id == graceful.transaction_id
        assert force.transaction_id != graceful.transaction_id
        assert force_preview.preview_id != preview.preview_id
        force_result = _execute(service, force, force_preview)
        assert force_result.all_exited
        assert platform.force_calls == [4_001]
    finally:
        repository.close()
        audit.close()


def test_restart_marks_inflight_transaction_interrupted(tmp_path: Path) -> None:
    platform = FakeProcessPlatform((process_observation(),))
    service, repository, audit, _registry = _service(tmp_path, platform)
    try:
        plan, preview, _review = service.prepare(
            "close demo",
            ProcessTargetQuery(query_type=ProcessTargetQueryType.PID, pid=4_001),
            ProcessActionType.REQUEST_GRACEFUL_EXIT,
        )
        first = service.request_plan_confirmation(plan, preview)
        service.resolve_plan_confirmation(first.confirmation_id, True, plan, preview)
        live, second = service.request_runtime_confirmation(first.confirmation_id, plan)
        service.resolve_runtime_confirmation(second.confirmation_id, True, plan, live)
        repository.transition(plan.transaction_id, ProcessActionState.VALIDATING)
        repository.close()
        restarted = ProcessActionRepository(tmp_path / "actions.db")
        interrupted = restarted.initialize()
        assert plan.transaction_id in interrupted
        assert restarted.get(plan.transaction_id).state is ProcessActionState.INTERRUPTED
        restarted.close()
    finally:
        audit.close()


def test_process_audit_contains_identity_but_never_command_line(tmp_path: Path) -> None:
    platform = FakeProcessPlatform((process_observation(),))
    service, repository, audit, _registry = _service(tmp_path, platform)
    try:
        service.prepare(
            "close demo --secret-argument",
            ProcessTargetQuery(query_type=ProcessTargetQueryType.PID, pid=4_001),
            ProcessActionType.REQUEST_GRACEFUL_EXIT,
        )
        rows = audit.list_recent(10)
        preview_row = next(row for row in rows if row.event_type == "process.previewed")
        rendered = str(preview_row.parameters).casefold()
        assert "process_name" in rendered
        assert "executable_path" in rendered
        assert "command_line" not in rendered
        assert "cmdline" not in rendered
    finally:
        repository.close()
        audit.close()
