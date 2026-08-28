from __future__ import annotations

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.system_diagnostics import SystemCollector
from pc_manager_agent.domain.system_optimization import OptimizationToolName
from pc_manager_agent.orchestration.system_optimization_planner import (
    SystemOptimizationPlanCompiler,
    is_optimization_request,
)


def test_disk_space_request_uses_storage_only_collectors(
    runtime: ApplicationRuntime,
) -> None:
    plan = SystemOptimizationPlanCompiler(runtime.authorized_paths).compile("C盘为什么满了？")
    assert plan.tools == (
        OptimizationToolName.SNAPSHOT,
        OptimizationToolName.STORAGE_ANALYZE,
        OptimizationToolName.CLEANUP_CANDIDATES_ANALYZE,
        OptimizationToolName.RECOMMENDATIONS,
    )
    assert plan.snapshot_collectors == (SystemCollector.DISKS,)
    assert is_optimization_request("C盘为什么满了？")


def test_slow_pc_request_skips_cache_collectors(runtime: ApplicationRuntime) -> None:
    plan = SystemOptimizationPlanCompiler(runtime.authorized_paths).compile("电脑为什么卡？")
    assert OptimizationToolName.STORAGE_ANALYZE not in plan.tools
    assert plan.snapshot_collectors == (
        SystemCollector.CPU,
        SystemCollector.MEMORY,
        SystemCollector.DISKS,
        SystemCollector.PROCESSES,
    )


def test_boot_request_does_not_expand_to_general_slow_pc(runtime: ApplicationRuntime) -> None:
    plan = SystemOptimizationPlanCompiler(runtime.authorized_paths).compile("开机慢")
    assert plan.snapshot_collectors == (
        SystemCollector.PROCESSES,
        SystemCollector.STARTUP,
    )
    assert OptimizationToolName.STORAGE_ANALYZE not in plan.tools


def test_general_check_keeps_complete_read_only_surface(runtime: ApplicationRuntime) -> None:
    plan = SystemOptimizationPlanCompiler(runtime.authorized_paths).compile("全面检查")
    assert plan.tools == tuple(OptimizationToolName)
    assert plan.snapshot_collectors == tuple(SystemCollector)


def test_quick_check_uses_small_counter_set(runtime: ApplicationRuntime) -> None:
    plan = SystemOptimizationPlanCompiler(runtime.authorized_paths).compile("快速检查")
    assert plan.tools == (
        OptimizationToolName.SNAPSHOT,
        OptimizationToolName.PERFORMANCE_ANALYZE,
        OptimizationToolName.RECOMMENDATIONS,
    )
    assert plan.snapshot_collectors == (
        SystemCollector.CPU,
        SystemCollector.MEMORY,
        SystemCollector.DISKS,
    )
