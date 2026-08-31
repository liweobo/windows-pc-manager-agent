"""Minimal domain-specific R0 refresh selection and non-attributable observations."""

from __future__ import annotations

from pc_manager_agent.domain.optimization_actions import (
    OptimizationBenefitObservation,
    OptimizationMetric,
    OptimizationTargetDomain,
)
from pc_manager_agent.domain.system_diagnostics import (
    CollectorState,
    SystemCollector,
    SystemSnapshot,
)
from pc_manager_agent.safety.optimization_actions import OptimizationRoutingError


def optimization_refresh_goal(domain: OptimizationTargetDomain) -> str:
    """Select one existing Stage 3 intent; never run the full optimization graph again."""
    goals = {
        OptimizationTargetDomain.STARTUP: "查看启动项",
        OptimizationTargetDomain.PROCESS: "查看内存",
        OptimizationTargetDomain.SOFTWARE: "查看已安装软件",
        OptimizationTargetDomain.SERVICE: "查看服务列表",
        OptimizationTargetDomain.SYSTEM_CLEANUP: "查看磁盘空间",
        OptimizationTargetDomain.SOFTWARE_RESIDUAL: "查看磁盘空间",
        OptimizationTargetDomain.PERSONAL_STORAGE: "查看磁盘空间",
        OptimizationTargetDomain.INFORMATIONAL: "查看磁盘空间",
    }
    try:
        return goals[domain]
    except KeyError as exc:
        raise OptimizationRoutingError("REFRESH_DOMAIN_UNSUPPORTED") from exc


def compare_optimization_observations(
    domain: OptimizationTargetDomain,
    before: SystemSnapshot,
    after: SystemSnapshot,
) -> tuple[OptimizationBenefitObservation, ...]:
    """Compare only complete same-kind reads; short observations cannot prove benefit.

    Disk changes are observations of volume free bytes, never bytes reclaimed by moving
    files to the Recycle Bin. Unknown/partial evidence stays unmeasured.
    """
    if after.collected_at <= before.collected_at:
        raise OptimizationRoutingError("REFRESH_NOT_NEWER_THAN_SOURCE")

    def complete(snapshot: SystemSnapshot, collector: SystemCollector) -> bool:
        matches = tuple(item for item in snapshot.outcomes if item.collector is collector)
        return len(matches) == 1 and matches[0].state is CollectorState.SUCCEEDED

    first: float | None = None
    second: float | None = None
    if domain is OptimizationTargetDomain.STARTUP:
        metric = OptimizationMetric.STARTUP_ENABLED_COUNT
        if (
            complete(before, SystemCollector.STARTUP)
            and complete(after, SystemCollector.STARTUP)
            and all(
                item.enabled is not None
                for item in (*before.startup_entries, *after.startup_entries)
            )
        ):
            first = float(sum(item.enabled is True for item in before.startup_entries))
            second = float(sum(item.enabled is True for item in after.startup_entries))
    elif domain is OptimizationTargetDomain.PROCESS:
        metric = OptimizationMetric.AVAILABLE_MEMORY_BYTES
        if (
            complete(before, SystemCollector.MEMORY)
            and complete(after, SystemCollector.MEMORY)
            and before.memory is not None
            and after.memory is not None
        ):
            first, second = (
                float(before.memory.available_bytes),
                float(after.memory.available_bytes),
            )
    elif domain is OptimizationTargetDomain.SOFTWARE:
        metric = OptimizationMetric.SOFTWARE_ENTRY_COUNT
        if complete(before, SystemCollector.SOFTWARE) and complete(after, SystemCollector.SOFTWARE):
            first, second = float(len(before.software)), float(len(after.software))
    elif domain in {
        OptimizationTargetDomain.SYSTEM_CLEANUP,
        OptimizationTargetDomain.SOFTWARE_RESIDUAL,
        OptimizationTargetDomain.PERSONAL_STORAGE,
        OptimizationTargetDomain.INFORMATIONAL,
    }:
        metric = OptimizationMetric.DISK_FREE_BYTES
        old = {(item.device, str(item.mountpoint).casefold()): item for item in before.disks}
        new = {(item.device, str(item.mountpoint).casefold()): item for item in after.disks}
        if (
            complete(before, SystemCollector.DISKS)
            and complete(after, SystemCollector.DISKS)
            and old
            and old.keys() == new.keys()
            and len(old) == len(before.disks)
            and len(new) == len(after.disks)
            and all(old[key].total_bytes == new[key].total_bytes for key in old)
        ):
            first, second = (
                float(sum(item.free_bytes for item in old.values())),
                float(sum(item.free_bytes for item in new.values())),
            )
    else:
        return ()
    return (
        OptimizationBenefitObservation(
            metric=metric,
            before=first,
            after=second,
            measured=first is not None and second is not None,
        ),
    )
