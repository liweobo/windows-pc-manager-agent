from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from pc_manager_agent.app.tasks import build_final_task_services
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.domain.computer_tasks import ComputerTaskState
from pc_manager_agent.domain.task_workflows import DomainPreparationResult


def test_restart_invalidates_consent_marks_interrupted_and_never_replays(tmp_path: Path) -> None:
    settings = AppSettings(data_directory=tmp_path / "app")
    audit = AuditRepository(settings.database_path)
    audit.initialize()
    first = build_final_task_services(settings, audit)
    template = first.templates.get("STARTUP_REVIEW")
    task = first.orchestrator.create_task(
        template.title,
        template.domains,
        root_request_id=uuid4(),
        kind=template.kind,
        autonomy=template.autonomy,
    )
    confirmation = first.orchestrator.request_plan_confirmation(task.task_id)
    first.orchestrator.resolve_plan_confirmation(confirmation.confirmation_id, approved=True)
    first.orchestrator.start(task.task_id, confirmation.confirmation_id)
    first.orchestrator.advance(task.task_id)
    prepared = first.orchestrator.advance(task.task_id)
    assert isinstance(prepared, DomainPreparationResult)
    first.orchestrator.register_domain_transaction(
        task.task_id,
        prepared.node_id,
        "startup-transaction-1",
    )
    first.close()

    second = build_final_task_services(settings, audit)
    try:
        assert second.interrupted_task_count == 1
        interrupted = second.orchestrator.list_recent()[0]
        assert interrupted.state is ComputerTaskState.INTERRUPTED
        results = second.orchestrator.recover(task.task_id)
        assert len(results) == 1
        assert not results[0].action_replayed
        recovered = second.orchestrator.list_recent()[0]
        assert recovered.state is ComputerTaskState.WAITING_FOR_USER
        assert any(
            item.title_code == "TASK_RECOVERY_REVIEW_REQUIRED"
            for item in second.orchestrator.pending_attention()
        )
    finally:
        second.close()
        audit.close()
