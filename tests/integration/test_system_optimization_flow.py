from __future__ import annotations

from tests.fixtures.system_optimization import FakeOptimizationPlatform, build_optimization_registry

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.audit.system_optimization import SystemOptimizationAuditLogger
from pc_manager_agent.confirmation.system_optimization import OptimizationConfirmationService
from pc_manager_agent.domain.system_optimization import OptimizationToolName
from pc_manager_agent.orchestration.system_optimization import SystemOptimizationOrchestrator
from pc_manager_agent.orchestration.system_optimization_planner import (
    SystemOptimizationPlanCompiler,
)
from pc_manager_agent.safety.system_optimization import SystemOptimizationSafetyValidator


def test_confirmed_flow_produces_report_and_aggregate_audit(
    runtime: ApplicationRuntime,
) -> None:
    platform = FakeOptimizationPlatform()
    registry = build_optimization_registry(platform)
    compiler = SystemOptimizationPlanCompiler(runtime.authorized_paths)
    confirmation = OptimizationConfirmationService()
    orchestrator = SystemOptimizationOrchestrator(
        registry=registry,
        compiler=compiler,
        safety=SystemOptimizationSafetyValidator(registry, runtime.authorized_paths),
        confirmation=confirmation,
        audit=SystemOptimizationAuditLogger(runtime.audit, git_commit="test"),
    )
    plan, review = orchestrator.prepare("释放磁盘空间并诊断电脑卡顿")
    assert review.approved
    request = orchestrator.request_confirmation(plan)
    orchestrator.resolve_confirmation(request.confirmation_id, True, plan)
    report = orchestrator.execute(plan)

    assert platform.snapshot_calls == 1
    assert platform.storage_calls == 1
    assert report.changes_performed is False
    assert report.stage4e1_executable is False
    assert report.cleanup_candidates
    assert report.performance_findings
    assert report.recommendations
    assert registry.names == tuple(sorted(item.value for item in OptimizationToolName))
    events = runtime.audit.list_recent(20)
    report_event = next(
        item for item in events if item.event_type == "optimization.report.completed"
    )
    assert "cleanup_candidates" not in report_event.result
    assert report_event.verification == {"read_only": True, "system_changes": 0}
    plan_event = next(item for item in events if item.event_type == "optimization.plan.reviewed")
    assert "释放磁盘" not in (plan_event.original_request or "")
    assert "authorized_roots" not in plan_event.plan


def test_disk_only_plan_does_not_run_performance_analysis(
    runtime: ApplicationRuntime,
) -> None:
    platform = FakeOptimizationPlatform()
    registry = build_optimization_registry(platform)
    compiler = SystemOptimizationPlanCompiler(runtime.authorized_paths)
    confirmation = OptimizationConfirmationService()
    orchestrator = SystemOptimizationOrchestrator(
        registry=registry,
        compiler=compiler,
        safety=SystemOptimizationSafetyValidator(registry, runtime.authorized_paths),
        confirmation=confirmation,
        audit=SystemOptimizationAuditLogger(runtime.audit, git_commit="test"),
    )
    plan, review = orchestrator.prepare("C盘为什么满了？")
    assert review.approved
    request = orchestrator.request_confirmation(plan)
    orchestrator.resolve_confirmation(request.confirmation_id, True, plan)
    report = orchestrator.execute(plan)

    assert platform.snapshot_calls == 1
    assert platform.storage_calls == 1
    assert report.cleanup_candidates
    assert report.performance_findings == ()
    assert report.snapshot.system.cpu is None
    assert report.snapshot.system.services == ()


def test_slow_pc_plan_does_not_scan_storage(runtime: ApplicationRuntime) -> None:
    platform = FakeOptimizationPlatform()
    registry = build_optimization_registry(platform)
    compiler = SystemOptimizationPlanCompiler(runtime.authorized_paths)
    confirmation = OptimizationConfirmationService()
    orchestrator = SystemOptimizationOrchestrator(
        registry=registry,
        compiler=compiler,
        safety=SystemOptimizationSafetyValidator(registry, runtime.authorized_paths),
        confirmation=confirmation,
        audit=SystemOptimizationAuditLogger(runtime.audit, git_commit="test"),
    )
    plan, review = orchestrator.prepare("电脑为什么卡？")
    assert review.approved
    request = orchestrator.request_confirmation(plan)
    orchestrator.resolve_confirmation(request.confirmation_id, True, plan)
    report = orchestrator.execute(plan)

    assert platform.snapshot_calls == 1
    assert platform.storage_calls == 0
    assert report.cleanup_candidates == ()
    assert report.performance_findings
