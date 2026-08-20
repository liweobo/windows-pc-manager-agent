from __future__ import annotations

from tests.fixtures.software_analysis import build_software_services, msi_entry

from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery


def test_confirmed_read_only_flow_generates_preview_and_never_uninstalls(tmp_path) -> None:
    services, repository, platform = build_software_services(
        tmp_path / "audit.sqlite3", (msi_entry(),)
    )
    plan, review = services.service.prepare(
        "卸载软件 Example App", SoftwareTargetQuery(display_name="Example App")
    )
    assert review.approved
    request = services.service.request_plan_confirmation(plan)
    services.service.resolve_plan_confirmation(request.confirmation_id, True, plan)
    outcome = services.service.analyze(plan)
    assert outcome.resolution.selected is not None
    assert outcome.preview is not None
    assert outcome.preview.capability.capability_type.value == "msi"
    assert outcome.preview.stop_reason.startswith("Stage 4D1 ends")
    assert not outcome.preview.execution_performed
    assert platform.entries == (msi_entry(),)
    events = repository.list_recent(20)
    assert {row.event_type for row in events} >= {
        "software.plan.reviewed",
        "software.plan_confirmation.resolved",
        "software.inventory.completed",
        "software.target.resolved",
        "software.preview.created",
    }
    assert all(
        not row.result or row.result.get("execution_performed") is not True for row in events
    )
    repository.close()


def test_ambiguous_flow_returns_candidates_without_preview(tmp_path) -> None:
    entries = (
        msi_entry(version="1.0"),
        msi_entry(version="2.0", product_code="{22345678-1234-1234-1234-1234567890AB}"),
    )
    services, repository, _platform = build_software_services(tmp_path / "audit.sqlite3", entries)
    plan, _review = services.service.prepare("卸载软件 Example App")
    request = services.service.request_plan_confirmation(plan)
    services.service.resolve_plan_confirmation(request.confirmation_id, True, plan)
    outcome = services.service.analyze(plan)
    assert outcome.preview is None
    assert outcome.resolution.ambiguous
    assert len(outcome.resolution.candidates) == 2
    repository.close()
