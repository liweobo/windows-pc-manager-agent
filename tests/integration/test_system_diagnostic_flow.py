from __future__ import annotations

from pathlib import Path

import pytest
from tests.fixtures.system_diagnostics import FakeSystemPlatform, build_registry

from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.audit.system_diagnostics import DiagnosticAuditLogger
from pc_manager_agent.confirmation.system_diagnostics import (
    DiagnosticConfirmationError,
    DiagnosticConfirmationService,
)
from pc_manager_agent.orchestration.diagnostic_engine import DiagnosticEngine
from pc_manager_agent.orchestration.system_diagnostic_planner import DiagnosticPlanCompiler
from pc_manager_agent.orchestration.system_diagnostics import (
    DiagnosticOrchestrator,
    SystemSnapshotService,
)
from pc_manager_agent.safety.system_diagnostics import DiagnosticSafetyValidator
from pc_manager_agent.tools.manifest import CancellationToken


def _orchestrator(
    database: Path, platform: FakeSystemPlatform
) -> tuple[DiagnosticOrchestrator, AuditRepository]:
    repository = AuditRepository(database)
    repository.initialize()
    registry = build_registry(platform)
    compiler = DiagnosticPlanCompiler(registry, sample_interval_seconds=0.1)
    confirmation = DiagnosticConfirmationService()
    audit = DiagnosticAuditLogger(repository)
    return (
        DiagnosticOrchestrator(
            compiler=compiler,
            safety=DiagnosticSafetyValidator(registry),
            confirmation=confirmation,
            snapshot_service=SystemSnapshotService(registry, audit),
            engine=DiagnosticEngine(),
            audit=audit,
        ),
        repository,
    )


def test_confirmed_diagnostic_flow_and_minimized_audit(tmp_path: Path) -> None:
    orchestrator, repository = _orchestrator(tmp_path / "audit.db", FakeSystemPlatform())
    plan, review = orchestrator.prepare("诊断电脑性能")
    assert review.approved
    with pytest.raises(DiagnosticConfirmationError):
        orchestrator.execute(plan)
    request = orchestrator.request_confirmation(plan)
    orchestrator.resolve_confirmation(request.confirmation_id, True, plan)
    report = orchestrator.execute(plan)
    assert report.snapshot.cpu is not None
    assert report.snapshot.memory is not None
    rows = repository.list_recent(100)
    types = {row.event_type for row in rows}
    assert "diagnostic.plan.reviewed" in types
    assert "diagnostic.collector.started" in types
    assert "diagnostic.report.completed" in types
    rendered = str([(row.parameters, row.result) for row in rows]).casefold()
    assert "--background" not in rendered
    assert "editor.exe" not in rendered
    repository.close()


def test_one_collector_failure_produces_partial_report(tmp_path: Path) -> None:
    orchestrator, repository = _orchestrator(
        tmp_path / "audit.db", FakeSystemPlatform(fail_cpu=True)
    )
    plan, _review = orchestrator.prepare("诊断电脑性能")
    request = orchestrator.request_confirmation(plan)
    orchestrator.resolve_confirmation(request.confirmation_id, True, plan)
    report = orchestrator.execute(plan)
    states = {outcome.collector.value: outcome.state.value for outcome in report.snapshot.outcomes}
    assert states["system.cpu"] == "failed"
    assert states["system.memory"] == "succeeded"
    assert report.snapshot.memory is not None
    repository.close()


def test_overview_executes_all_registered_collectors(tmp_path: Path) -> None:
    platform = FakeSystemPlatform()
    orchestrator, repository = _orchestrator(tmp_path / "audit.db", platform)
    plan, review = orchestrator.prepare("查看电脑系统状态")
    assert review.approved
    request = orchestrator.request_confirmation(plan)
    orchestrator.resolve_confirmation(request.confirmation_id, True, plan)
    report = orchestrator.execute(plan)
    assert len(report.snapshot.outcomes) == 8
    assert report.snapshot.startup_entries
    assert report.snapshot.services
    assert report.snapshot.software
    assert platform.cpu_call == (3, 0.1)
    assert platform.process_call == (0.1, 500)
    repository.close()


def test_pre_cancelled_diagnostic_starts_no_collector(tmp_path: Path) -> None:
    orchestrator, repository = _orchestrator(tmp_path / "audit.db", FakeSystemPlatform())
    plan, _review = orchestrator.prepare("查看电脑系统状态")
    request = orchestrator.request_confirmation(plan)
    orchestrator.resolve_confirmation(request.confirmation_id, True, plan)
    token = CancellationToken()
    token.cancel()
    report = orchestrator.execute(plan, token)
    assert all(outcome.state.value == "cancelled" for outcome in report.snapshot.outcomes)
    assert not any(
        row.event_type == "diagnostic.collector.started" for row in repository.list_recent()
    )
    repository.close()
