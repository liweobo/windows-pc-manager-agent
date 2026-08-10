from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.file_analysis import (
    AnalysisType,
    FileAnalysisFilters,
    FileAnalysisIntentDraft,
)
from pc_manager_agent.reporting.exporter import ReportExportError, ReportFormat
from pc_manager_agent.tools.manifest import CancellationToken


def execute_report(runtime: ApplicationRuntime, root: Path) -> tuple[object, object]:
    record = runtime.authorized_paths.add_authorized(root)
    services = runtime.create_file_analysis_services()
    plan = services.compiler.compile(
        "导出大文件报告",
        FileAnalysisIntentDraft(
            authorized_root_ids=(record.path_id,),
            filters=FileAnalysisFilters(minimum_size_bytes=1, inactive_days=90),
            analyses=(AnalysisType.LARGE_FILES,),
        ),
    )
    confirmation = services.orchestrator.request_plan_confirmation(plan)
    services.orchestrator.resolve_plan_confirmation(
        confirmation.confirmation_id,
        True,
        plan,
    )
    return plan, services.orchestrator.execute(plan, CancellationToken())


def test_csv_and_json_reports_are_exclusive_and_structured(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "scan"
    root.mkdir()
    (root / "file.txt").write_text("hello", encoding="utf-8")
    plan, report = execute_report(runtime, root)
    csv_path = tmp_path / "report.csv"
    json_path = tmp_path / "report.json"

    csv_result = runtime.report_exporter.export(csv_path, ReportFormat.CSV, plan, report)
    json_result = runtime.report_exporter.export(json_path, ReportFormat.JSON, plan, report)

    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert csv_result.rows == 1
    assert rows[0]["name"] == "file.txt"
    assert json_result.rows == 1
    assert payload["summary"]["matching_files"] == 1
    assert payload["files"][0]["name"] == "file.txt"
    with pytest.raises(ReportExportError, match="never overwritten"):
        runtime.report_exporter.export(csv_path, ReportFormat.CSV, plan, report)
