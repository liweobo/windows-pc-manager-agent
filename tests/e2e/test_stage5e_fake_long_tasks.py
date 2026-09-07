from __future__ import annotations

from uuid import uuid4

import pytest

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.app.tasks import FinalTaskServices
from pc_manager_agent.domain.computer_tasks import ComputerTaskState
from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.domain.task_summaries import StructuredTaskSummary
from pc_manager_agent.domain.task_workflows import (
    DomainPreparationResult,
    DomainResultReceipt,
    DomainResultStatus,
    DomainVerificationStatus,
)


def _run_fake_task(services: FinalTaskServices, template_code: str) -> StructuredTaskSummary:
    template = services.templates.get(template_code)
    task = services.orchestrator.create_task(
        template.title,
        template.domains,
        root_request_id=uuid4(),
        kind=template.kind,
        autonomy=template.autonomy,
    )
    confirmation = services.orchestrator.request_plan_confirmation(task.task_id)
    services.orchestrator.resolve_plan_confirmation(confirmation.confirmation_id, approved=True)
    services.orchestrator.start(task.task_id, confirmation.confirmation_id)
    assert services.orchestrator.advance(task.task_id) is None
    for index, expected_domain in enumerate(template.domains, start=1):
        prepared = services.orchestrator.advance(task.task_id)
        assert isinstance(prepared, DomainPreparationResult)
        assert prepared.domain is expected_domain
        services.orchestrator.record_domain_receipt(
            DomainResultReceipt(
                task_id=task.task_id,
                node_id=prepared.node_id,
                graph_version=1,
                domain=prepared.domain,
                status=DomainResultStatus.COMPLETED_VERIFIED,
                verification_status=DomainVerificationStatus.VERIFIED,
                result_ref=f"fake-result-{index}",
                verification_ref=f"fake-verification-{index}",
                result_code=f"{prepared.domain.value}_FAKE_VERIFIED",
                unchanged_count=index,
                rollback_level=RollbackLevel.NONE,
            )
        )
    summary = services.orchestrator.advance(task.task_id)
    assert isinstance(summary, StructuredTaskSummary)
    return summary


@pytest.mark.parametrize(
    "template_code,expected_receipts",
    (
        ("PC_HEALTH_CHECK", 2),
        ("HEALTH_REPORT", 3),
        ("WEB_RESEARCH_REPORT", 2),
    ),
)
def test_fake_long_task_scenarios_never_use_windows_writers(
    runtime: ApplicationRuntime,
    template_code: str,
    expected_receipts: int,
) -> None:
    summary = _run_fake_task(runtime.tasks, template_code)
    assert summary.state is ComputerTaskState.COMPLETED
    assert summary.completed_verified == expected_receipts
    assert summary.changed_count == 0
    assert not summary.global_undo_available
