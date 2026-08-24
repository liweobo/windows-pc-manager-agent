"""Exclusive-create export tests for Stage 4D3 local reports."""

from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.reporting.residual_exporter import (
    ResidualExportError,
    ResidualReportExporter,
    ResidualReportFormat,
)
from tests.fixtures.software_residuals import (
    build_residual_environment,
    residual_context,
)


def test_json_export_is_user_created_and_never_overwritten(tmp_path: Path) -> None:
    root = tmp_path / "app"
    root.mkdir()
    (root / "cache.tmp").write_bytes(b"cache")
    environment = build_residual_environment(tmp_path / "state.db")
    context = residual_context(root)
    environment.repository.upsert_context(context)
    try:
        plan, context, _review = environment.service.prepare("只读报告", context.transaction_id)
        confirmation = environment.service.request_plan_confirmation(plan, context)
        environment.service.resolve_plan_confirmation(
            confirmation.confirmation_id, True, plan, context
        )
        report = environment.service.analyze(plan, context)
        target = tmp_path / "residuals.json"
        exporter = ResidualReportExporter()
        result = environment.service.export_report(
            plan,
            context,
            report,
            target,
            ResidualReportFormat.JSON,
            exporter,
        )
        assert result.target == target
        assert '"deletion_performed": false' in target.read_text(encoding="utf-8")
        with pytest.raises(ResidualExportError, match="never overwritten"):
            ResidualReportExporter().export(report, target, ResidualReportFormat.JSON)
        events = environment.audit.list_recent(20)
        exported = next(
            event for event in events if event.event_type == "software.residuals.report.exported"
        )
        assert str(target) not in str(exported.parameters)
        assert exported.result is not None
        assert exported.result["overwrite_performed"] is False
    finally:
        environment.close()


def test_csv_export_and_invalid_destination_boundaries(tmp_path: Path) -> None:
    root = tmp_path / "app"
    root.mkdir()
    (root / "data.db").write_bytes(b"database")
    environment = build_residual_environment(tmp_path / "state-csv.db")
    context = residual_context(root)
    environment.repository.upsert_context(context)
    try:
        plan, loaded, _review = environment.service.prepare("只读报告", context.transaction_id)
        confirmation = environment.service.request_plan_confirmation(plan, loaded)
        environment.service.resolve_plan_confirmation(
            confirmation.confirmation_id, True, plan, loaded
        )
        report = environment.service.analyze(plan, loaded)
        target = tmp_path / "residuals.csv"
        result = ResidualReportExporter().export(report, target, ResidualReportFormat.CSV)
        assert result.candidate_count == len(report.candidates)
        exported = target.read_text(encoding="utf-8-sig")
        assert "ownership_confidence" in exported
        assert str(root / "data.db") in exported
        with pytest.raises(ResidualExportError, match="absolute"):
            ResidualReportExporter().export(
                report, Path("relative.json"), ResidualReportFormat.JSON
            )
        with pytest.raises(ResidualExportError, match="does not exist"):
            ResidualReportExporter().export(
                report, tmp_path / "missing" / "report.json", ResidualReportFormat.JSON
            )
        with pytest.raises(ResidualExportError, match="end with"):
            ResidualReportExporter().export(
                report, tmp_path / "wrong.txt", ResidualReportFormat.JSON
            )
    finally:
        environment.close()
