from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.confirmation.state_machine import ConfirmationError
from pc_manager_agent.domain.reports import ScanReport, ScanSummary
from pc_manager_agent.orchestration.service import OrchestrationError, ScanOrchestrator
from pc_manager_agent.tools.manifest import CancellationToken


def test_complete_scan_workflow_is_confirmed_audited_and_verified(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    (root / "a.txt").write_text("abc", encoding="utf-8")
    orchestrator = runtime.create_scan_orchestrator(root)
    plan, review = orchestrator.prepare_plan(root)
    assert review.approved
    request = orchestrator.request_plan_confirmation(plan)
    orchestrator.resolve_plan_confirmation(request.confirmation_id, True, plan)
    report = orchestrator.execute(plan, CancellationToken())
    assert report.summary.files_seen == 1
    assert [row.event_type for row in runtime.audit.list_recent()] == [
        "tool.completed",
        "tool.started",
        "confirmation.resolved",
        "plan.reviewed",
    ]


def test_execution_without_or_after_rejected_confirmation_fails(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    orchestrator = runtime.create_scan_orchestrator(tmp_path)
    plan, _ = orchestrator.prepare_plan(tmp_path)
    with pytest.raises(ConfirmationError):
        orchestrator.execute(plan, CancellationToken())
    request = orchestrator.request_plan_confirmation(plan)
    orchestrator.resolve_plan_confirmation(request.confirmation_id, False, plan)
    with pytest.raises(ConfirmationError):
        orchestrator.execute(plan, CancellationToken())


def test_verifier_rejects_mismatched_root_and_count(tmp_path: Path) -> None:
    plan_root = tmp_path / "plan"
    report_root = tmp_path / "report"
    plan_root.mkdir()
    report_root.mkdir()
    from tests.unit.test_models import build_plan

    plan = build_plan(plan_root)
    report = ScanReport(
        root=report_root,
        files=(),
        issues=(),
        summary=ScanSummary(
            files_seen=0,
            directories_seen=1,
            total_size_bytes=0,
            issues=0,
            duration_ms=0,
        ),
    )
    with pytest.raises(OrchestrationError, match="root"):
        ScanOrchestrator._verify(plan, report)
    bad_count = report.model_copy(
        update={"root": plan_root, "summary": report.summary.model_copy(update={"files_seen": 1})}
    )
    with pytest.raises(OrchestrationError, match="count"):
        ScanOrchestrator._verify(plan, bad_count)
