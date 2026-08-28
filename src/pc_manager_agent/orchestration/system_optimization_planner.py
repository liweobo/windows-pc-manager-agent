"""Local finite planner for Stage 4E1 read-only analysis."""

from __future__ import annotations

from uuid import UUID

from pc_manager_agent.authorization.service import AuthorizedPathService
from pc_manager_agent.domain.system_diagnostics import SystemCollector
from pc_manager_agent.domain.system_optimization import (
    OptimizationGoal,
    OptimizationPlan,
    OptimizationToolName,
)


def is_optimization_request(text: str) -> bool:
    """Return whether text asks for cleanup analysis or performance optimization advice."""
    normalized = text.casefold()
    markers = (
        "系统清理",
        "清理分析",
        "释放空间",
        "空间回收",
        "磁盘满",
        "c盘",
        "电脑变慢",
        "电脑卡",
        "优化建议",
        "快速检查",
        "全面检查",
        "开机慢",
        "后台负载",
        "system cleanup",
        "free disk space",
        "slow pc",
        "optimize performance",
        "quick check",
        "health check",
    )
    return any(marker in normalized for marker in markers)


class SystemOptimizationPlanCompiler:
    """Resolve path IDs and choose the smallest deterministic read-only collector set."""

    def __init__(
        self,
        authorized_paths: AuthorizedPathService,
        *,
        max_objects: int = 25_000,
        timeout_seconds: float = 60.0,
        minimum_large_file_bytes: int = 1024**3,
        inactive_days: int = 90,
        sample_count: int = 3,
        sample_interval_seconds: float = 0.5,
    ) -> None:
        self._authorized_paths = authorized_paths
        self._max_objects = max_objects
        self._timeout_seconds = timeout_seconds
        self._minimum_large_file_bytes = minimum_large_file_bytes
        self._inactive_days = inactive_days
        self._sample_count = sample_count
        self._sample_interval_seconds = sample_interval_seconds

    def compile(
        self, user_goal: str, authorized_root_ids: tuple[UUID, ...] = ()
    ) -> OptimizationPlan:
        """Create a deterministic plan; path text from the user is never accepted here."""
        goal = user_goal.strip()
        if not goal:
            raise ValueError("Optimization goal cannot be empty")
        roots = self._authorized_paths.resolve_authorized(authorized_root_ids)
        goals = self._goals(goal)
        quick_check = any(marker in goal.casefold() for marker in ("快速", "quick"))
        tools = self._tools(goals, quick_check=quick_check)
        collectors = self._collectors(goals, quick_check=quick_check)
        return OptimizationPlan(
            user_goal=goal,
            summary=(
                "只读采集系统状态和已允许位置的文件元数据，生成空间候选、性能发现和建议；"
                "系统与用户数据修改数量固定为 0。"
            ),
            goals=goals,
            tools=tools,
            snapshot_collectors=collectors,
            authorized_root_ids=authorized_root_ids,
            authorized_roots=tuple(item.path for item in roots),
            max_objects=self._max_objects,
            timeout_seconds=self._timeout_seconds,
            minimum_large_file_bytes=self._minimum_large_file_bytes,
            inactive_days=self._inactive_days,
            sample_count=self._sample_count,
            sample_interval_seconds=self._sample_interval_seconds,
        )

    @staticmethod
    def _goals(text: str) -> tuple[OptimizationGoal, ...]:
        normalized = text.casefold()
        selected: list[OptimizationGoal] = []
        boot_requested = any(marker in normalized for marker in ("开机", "启动", "boot"))
        mappings = (
            (OptimizationGoal.FREE_DISK_SPACE, ("空间", "清理", "磁盘", "c盘", "disk")),
            (OptimizationGoal.REDUCE_BACKGROUND_LOAD, ("后台", "background")),
            (OptimizationGoal.IMPROVE_RESPONSIVENESS, ("响应", "responsiveness")),
        )
        for mapped_goal, markers in mappings:
            if any(marker in normalized for marker in markers):
                selected.append(mapped_goal)
        if boot_requested:
            selected.append(OptimizationGoal.IMPROVE_BOOT_TIME)
        elif any(marker in normalized for marker in ("慢", "卡", "slow")):
            selected.append(OptimizationGoal.DIAGNOSE_SLOW_PC)
        return tuple(selected) or (OptimizationGoal.GENERAL_HEALTH_CHECK,)

    @staticmethod
    def _tools(
        goals: tuple[OptimizationGoal, ...], *, quick_check: bool = False
    ) -> tuple[OptimizationToolName, ...]:
        """Map finite goals to the minimal dependency-complete tool subset."""
        general = OptimizationGoal.GENERAL_HEALTH_CHECK in goals and not quick_check
        storage = general or OptimizationGoal.FREE_DISK_SPACE in goals
        performance = (
            quick_check
            or general
            or any(
                item
                in {
                    OptimizationGoal.IMPROVE_BOOT_TIME,
                    OptimizationGoal.DIAGNOSE_SLOW_PC,
                    OptimizationGoal.REDUCE_BACKGROUND_LOAD,
                    OptimizationGoal.IMPROVE_RESPONSIVENESS,
                }
                for item in goals
            )
        )
        selected = {
            OptimizationToolName.SNAPSHOT,
            OptimizationToolName.RECOMMENDATIONS,
        }
        if storage:
            selected.update(
                {
                    OptimizationToolName.STORAGE_ANALYZE,
                    OptimizationToolName.CLEANUP_CANDIDATES_ANALYZE,
                }
            )
        if performance:
            selected.add(OptimizationToolName.PERFORMANCE_ANALYZE)
        return tuple(item for item in OptimizationToolName if item in selected)

    @staticmethod
    def _collectors(
        goals: tuple[OptimizationGoal, ...], *, quick_check: bool = False
    ) -> tuple[SystemCollector, ...]:
        """Select only system sources that can support the requested goals."""
        if quick_check:
            return (
                SystemCollector.CPU,
                SystemCollector.MEMORY,
                SystemCollector.DISKS,
            )
        if OptimizationGoal.GENERAL_HEALTH_CHECK in goals:
            return tuple(SystemCollector)
        selected: set[SystemCollector] = set()
        for goal in goals:
            if goal is OptimizationGoal.FREE_DISK_SPACE:
                selected.add(SystemCollector.DISKS)
            elif goal is OptimizationGoal.IMPROVE_BOOT_TIME:
                selected.update({SystemCollector.PROCESSES, SystemCollector.STARTUP})
            elif goal is OptimizationGoal.DIAGNOSE_SLOW_PC:
                selected.update(
                    {
                        SystemCollector.CPU,
                        SystemCollector.MEMORY,
                        SystemCollector.DISKS,
                        SystemCollector.PROCESSES,
                    }
                )
            elif goal is OptimizationGoal.REDUCE_BACKGROUND_LOAD:
                selected.update(
                    {
                        SystemCollector.CPU,
                        SystemCollector.MEMORY,
                        SystemCollector.PROCESSES,
                    }
                )
            elif goal is OptimizationGoal.IMPROVE_RESPONSIVENESS:
                selected.update(
                    {
                        SystemCollector.CPU,
                        SystemCollector.MEMORY,
                        SystemCollector.DISKS,
                        SystemCollector.PROCESSES,
                    }
                )
        return tuple(item for item in SystemCollector if item in selected)
