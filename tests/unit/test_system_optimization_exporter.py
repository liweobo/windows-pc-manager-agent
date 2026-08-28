from __future__ import annotations

import json
from pathlib import Path

import pytest

from pc_manager_agent.domain.system_optimization import (
    OptimizationGoal,
    OptimizationPlan,
    OptimizationSnapshot,
    OptimizationToolName,
    SystemOptimizationReport,
)
from pc_manager_agent.reporting.exporter import ReportExportError, ReportFormat
from pc_manager_agent.reporting.system_optimization_exporter import (
    SystemOptimizationReportExporter,
)
from tests.fixtures.system_optimization import build_system_snapshot


def _report() -> SystemOptimizationReport:
    plan = OptimizationPlan(
        user_goal="health",
        summary="read only",
        goals=(OptimizationGoal.GENERAL_HEALTH_CHECK,),
        tools=tuple(OptimizationToolName),
    )
    return SystemOptimizationReport(
        plan_id=plan.plan_id,
        plan_digest=plan.canonical_digest(),
        snapshot=OptimizationSnapshot(system=build_system_snapshot()),
        cleanup_candidates=(),
        performance_findings=(),
        recommendations=(),
        observed_bytes=0,
        protected_bytes=0,
        unknown_bytes=0,
    )


def test_json_export_is_explicit_and_never_overwrites(tmp_path: Path) -> None:
    exporter = SystemOptimizationReportExporter()
    target = tmp_path / "report.json"
    result = exporter.export(target, ReportFormat.JSON, _report())
    assert result.rows == 0
    assert json.loads(target.read_text(encoding="utf-8"))["changes_performed"] is False
    with pytest.raises(ReportExportError, match="never overwritten"):
        exporter.export(target, ReportFormat.JSON, _report())
