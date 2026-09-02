from __future__ import annotations

from pathlib import Path

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.domain.agents import AgentRole
from pc_manager_agent.domain.task_graph import TaskDomain
from pc_manager_agent.domain.task_outcomes import RootTaskStatus
from pc_manager_agent.domain.user_requests import (
    RequestChannel,
    RequestDomain,
    RequestRoute,
    UserRequest,
)


def test_single_domain_handoff_never_authorizes_execution(runtime: ApplicationRuntime) -> None:
    request = UserRequest(channel=RequestChannel.TEXT, text="show C drive free space")
    route = RequestRoute(request_id=request.request_id, domain=RequestDomain.DIAGNOSTICS)
    prepared = runtime.agents.runtime.prepare_request(request, route)
    assert prepared.selected_roles == (AgentRole.ORCHESTRATOR, AgentRole.SYSTEM)
    assert prepared.boundary.allowed_domains == (TaskDomain.SYSTEM,)
    result = runtime.agents.runtime.prepare_domain_handoff(
        prepared.graph.task_id, TaskDomain.SYSTEM
    )
    assert len(result.preparation_proposals) == 1
    assert result.preparation_proposals[0].execution_authorized is False
    journal = runtime.agents.runtime.coordinator.list_recent(1)[0]
    assert journal.status is RootTaskStatus.WAITING_CONFIRMATION
    assert request.text not in journal.model_dump_json()


def test_cancellation_stops_future_coordination_without_undo(runtime: ApplicationRuntime) -> None:
    prepared = runtime.agents.runtime.prepare_domains("read files", (TaskDomain.FILE,))
    cancelled = runtime.agents.runtime.cancel(prepared.graph.task_id)
    assert cancelled.status is RootTaskStatus.CANCELLED
    assert cancelled.completed_count == 0


def test_restart_marks_active_task_interrupted_and_does_not_restore_goal(tmp_path: Path) -> None:
    settings = AppSettings(data_directory=tmp_path / "app")
    first = ApplicationRuntime(settings)
    prepared = first.agents.runtime.prepare_domains("private volatile goal", (TaskDomain.SYSTEM,))
    task_id = prepared.graph.task_id
    first.close()

    second = ApplicationRuntime(settings)
    try:
        entry = next(
            item
            for item in second.agents.runtime.coordinator.list_recent()
            if item.task_id == task_id
        )
        assert entry.status is RootTaskStatus.INTERRUPTED
        assert "private volatile goal" not in entry.model_dump_json()
        assert second.agents.interrupted_task_count >= 1
    finally:
        second.close()


def test_agent_audit_contains_digests_not_goal(runtime: ApplicationRuntime) -> None:
    goal = "sensitive local filename should remain volatile"
    runtime.agents.runtime.prepare_domains(goal, (TaskDomain.FILE,))
    row = next(item for item in runtime.audit.list_recent() if item.event_type == "agent.task")
    serialized = str(row.parameters)
    assert goal not in serialized
    assert row.parameters["prompt_body_saved"] is False
    assert row.parameters["context_body_saved"] is False
    assert row.parameters["prompt_versions"]
    assert row.parameters["manifest_digests"]
