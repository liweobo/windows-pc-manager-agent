from __future__ import annotations

from uuid import uuid4

import pytest

from pc_manager_agent.domain.computer_tasks import (
    ComputerTaskState,
    TaskRevisionRequest,
)
from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.domain.task_summaries import StructuredTaskSummary
from pc_manager_agent.domain.task_workflows import (
    DomainPreparationResult,
    DomainResultReceipt,
    DomainResultStatus,
    DomainVerificationStatus,
)
from pc_manager_agent.orchestration.final_orchestrator import FinalOrchestratorError
from pc_manager_agent.safety.final_orchestrator import FinalOrchestratorSafetyError


def _create_started(task_services, template_code: str = "PC_HEALTH_CHECK"):
    template = task_services.templates.get(template_code)
    task = task_services.orchestrator.create_task(
        template.title,
        template.domains,
        root_request_id=uuid4(),
        kind=template.kind,
        autonomy=template.autonomy,
    )
    confirmation = task_services.orchestrator.request_plan_confirmation(task.task_id)
    task_services.orchestrator.resolve_plan_confirmation(
        confirmation.confirmation_id,
        approved=True,
    )
    task = task_services.orchestrator.start(task.task_id, confirmation.confirmation_id)
    return task, confirmation


def test_plan_confirmation_is_single_use_and_domain_result_drives_success(task_services) -> None:
    task, confirmation = _create_started(task_services, "STARTUP_REVIEW")
    with pytest.raises(FinalOrchestratorError):
        task_services.orchestrator.start(task.task_id, confirmation.confirmation_id)
    assert task_services.orchestrator.advance(task.task_id) is None
    prepared = task_services.orchestrator.advance(task.task_id)
    assert isinstance(prepared, DomainPreparationResult)
    waiting = task_services.orchestrator.list_recent()[0]
    assert waiting.state is ComputerTaskState.WAITING_FOR_USER
    receipt = DomainResultReceipt(
        task_id=task.task_id,
        node_id=prepared.node_id,
        graph_version=1,
        domain=prepared.domain,
        status=DomainResultStatus.COMPLETED_VERIFIED,
        verification_status=DomainVerificationStatus.VERIFIED,
        result_ref="startup-result",
        verification_ref="startup-verification",
        result_code="STARTUP_REVIEW_VERIFIED",
        unchanged_count=3,
        rollback_level=RollbackLevel.NONE,
    )
    changed = task_services.orchestrator.record_domain_receipt(receipt)
    assert changed.state is ComputerTaskState.RUNNING
    result = task_services.orchestrator.advance(task.task_id)
    assert isinstance(result, StructuredTaskSummary)
    assert result.state is ComputerTaskState.COMPLETED
    assert result.completed_verified == 1
    assert not result.global_undo_available


def test_duplicate_receipt_and_wrong_node_are_rejected(task_services) -> None:
    task, _ = _create_started(task_services, "STARTUP_REVIEW")
    task_services.orchestrator.advance(task.task_id)
    prepared = task_services.orchestrator.advance(task.task_id)
    assert isinstance(prepared, DomainPreparationResult)
    wrong = DomainResultReceipt(
        task_id=task.task_id,
        node_id=uuid4(),
        graph_version=1,
        domain=prepared.domain,
        status=DomainResultStatus.BLOCKED,
        verification_status=DomainVerificationStatus.UNVERIFIED,
        result_code="WRONG_NODE",
    )
    with pytest.raises(FinalOrchestratorSafetyError):
        task_services.orchestrator.record_domain_receipt(wrong)


def test_revision_invalidates_old_plan_and_requires_scope_expansion(task_services) -> None:
    template = task_services.templates.get("STARTUP_REVIEW")
    task = task_services.orchestrator.create_task(
        template.title,
        template.domains,
        root_request_id=uuid4(),
        kind=template.kind,
        autonomy=template.autonomy,
    )
    confirmation = task_services.orchestrator.request_plan_confirmation(task.task_id)
    with pytest.raises(FinalOrchestratorSafetyError):
        task_services.orchestrator.revise(
            TaskRevisionRequest(
                task_id=task.task_id,
                expected_graph_version=1,
                new_goal="Review startup and services",
                requested_domain_codes=("STARTUP", "SERVICE"),
            )
        )
    revised = task_services.orchestrator.revise(
        TaskRevisionRequest(
            task_id=task.task_id,
            expected_graph_version=1,
            new_goal="Review startup safely",
            requested_domain_codes=("STARTUP",),
        )
    )
    assert revised.graph_version == 2
    assert revised.state is ComputerTaskState.AWAITING_PLAN_CONFIRMATION
    with pytest.raises(FinalOrchestratorSafetyError):
        task_services.orchestrator.resolve_plan_confirmation(
            confirmation.confirmation_id,
            approved=True,
        )


def test_pause_resume_requires_fresh_user_review_and_cancel_is_future_only(task_services) -> None:
    task, _ = _create_started(task_services)
    paused = task_services.orchestrator.pause(task.task_id)
    assert paused.state is ComputerTaskState.PAUSED
    waiting = task_services.orchestrator.resume(task.task_id)
    assert waiting.state is ComputerTaskState.WAITING_FOR_USER
    assert task_services.orchestrator.pending_attention()
    cancelled = task_services.orchestrator.cancel(task.task_id)
    assert cancelled.state is ComputerTaskState.CANCELLED
    assert cancelled.cancellation_requested
    with pytest.raises(FinalOrchestratorError):
        task_services.orchestrator.cancel(task.task_id)


def test_cancel_before_start_remains_durably_readable(task_services) -> None:
    template = task_services.templates.get("STARTUP_REVIEW")
    task = task_services.orchestrator.create_task(
        template.title,
        template.domains,
        root_request_id=uuid4(),
        kind=template.kind,
        autonomy=template.autonomy,
    )

    cancelled = task_services.orchestrator.cancel(task.task_id)
    reloaded = task_services.orchestrator.list_recent()[0]

    assert cancelled.state is ComputerTaskState.CANCELLED
    assert cancelled.started_at is None
    assert reloaded == cancelled


def test_domain_transaction_reference_is_opaque_and_bound_to_active_node(task_services) -> None:
    task, _ = _create_started(task_services, "STARTUP_REVIEW")
    task_services.orchestrator.advance(task.task_id)
    prepared = task_services.orchestrator.advance(task.task_id)
    assert isinstance(prepared, DomainPreparationResult)
    with pytest.raises(FinalOrchestratorError):
        task_services.orchestrator.register_domain_transaction(
            task.task_id,
            prepared.node_id,
            "contains a path\\and spaces",
        )
    changed = task_services.orchestrator.register_domain_transaction(
        task.task_id,
        prepared.node_id,
        "startup-transaction-1",
    )
    assert changed.current_node_id == prepared.node_id
    with pytest.raises(FinalOrchestratorError):
        task_services.orchestrator.register_domain_transaction(
            task.task_id,
            uuid4(),
            "startup-transaction-2",
        )
