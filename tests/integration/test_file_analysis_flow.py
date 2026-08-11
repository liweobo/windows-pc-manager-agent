from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.file_analysis import (
    AnalysisType,
    FileAnalysisFilters,
    FileAnalysisIntentDraft,
)
from pc_manager_agent.domain.reports import ScanStatus
from pc_manager_agent.tools.manifest import CancellationToken


def build_plan(runtime: ApplicationRuntime, root: Path) -> tuple[object, object]:
    record = runtime.authorized_paths.add_authorized(root, label="Downloads")
    services = runtime.create_file_analysis_services()
    plan = services.compiler.compile(
        "找出大文件",
        FileAnalysisIntentDraft(
            authorized_root_ids=(record.path_id,),
            filters=FileAnalysisFilters(minimum_size_bytes=1, inactive_days=90),
            analyses=(AnalysisType.LARGE_FILES,),
        ),
    )
    return services, plan


def test_complete_confirmed_file_analysis_vertical_slice(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    (root / "one.txt").write_text("one", encoding="utf-8")
    (root / "two.bin").write_bytes(b"22")
    services, plan = build_plan(runtime, root)

    review = services.orchestrator.review(plan)
    confirmation = services.orchestrator.request_plan_confirmation(plan)
    services.orchestrator.resolve_plan_confirmation(
        confirmation.confirmation_id,
        True,
        plan,
    )
    report = services.orchestrator.execute(plan, CancellationToken())

    assert review.approved
    assert report.summary.status is ScanStatus.COMPLETED
    assert report.summary.files_scanned == 2
    assert report.summary.matching_files == 2
    assert report.summary.matching_bytes == 5
    rows = runtime.analysis_results.page_candidates(report.analysis_session_id)
    assert {row.metadata.name for row in rows} == {"one.txt", "two.bin"}
    events = runtime.audit.list_recent(100)
    assert any(event.event_type == "file_analysis.completed" for event in events)


def test_cancelled_analysis_is_a_terminal_partial_result(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    (root / "one.txt").write_text("one", encoding="utf-8")
    services, plan = build_plan(runtime, root)
    confirmation = services.orchestrator.request_plan_confirmation(plan)
    services.orchestrator.resolve_plan_confirmation(
        confirmation.confirmation_id,
        True,
        plan,
    )
    token = CancellationToken()
    token.cancel()

    report = services.orchestrator.execute(plan, token)

    assert report.summary.status is ScanStatus.CANCELLED
    assert report.summary.files_scanned == 0
    assert report.summary.matching_files == 0


def test_tool_failure_is_audited_and_aborts_execution(
    runtime: ApplicationRuntime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    services, plan = build_plan(runtime, root)
    confirmation = services.orchestrator.request_plan_confirmation(plan)
    services.orchestrator.resolve_plan_confirmation(
        confirmation.confirmation_id,
        True,
        plan,
    )

    def fail_execute(*_args: object, **_kwargs: object) -> object:
        raise PermissionError("simulated read denial")

    monkeypatch.setattr(services.registry, "execute", fail_execute)
    with pytest.raises(PermissionError, match="simulated"):
        services.orchestrator.execute(plan, CancellationToken())

    events = runtime.audit.list_recent(100)
    failed = next(event for event in events if event.event_type == "file_analysis.tool.failed")
    assert failed.tool_name == "file.scan"
    assert failed.error is not None
    assert failed.error["type"] == "PermissionError"
