"""Conservative multi-factor performance rules for Stage 4E1."""

from __future__ import annotations

from pc_manager_agent.domain.system_diagnostics import SystemSnapshot
from pc_manager_agent.domain.system_optimization import (
    OptimizationConfidence,
    OptimizationEvidence,
    PerformanceCategory,
    PerformanceFinding,
)


class PerformanceDiagnosticEngine:
    """Describe measured pressure without claiming a root cause or changing state."""

    def analyze(self, snapshot: SystemSnapshot) -> tuple[PerformanceFinding, ...]:
        """Evaluate available counters and return an explicit no-bottleneck result if needed."""
        findings: list[PerformanceFinding] = []
        cpu = snapshot.cpu
        if cpu is not None and cpu.average_percent >= 75 and cpu.peak_percent >= 85:
            findings.append(
                PerformanceFinding(
                    category=PerformanceCategory.CPU_PRESSURE,
                    title="CPU 在短时多次采样中持续偏高",
                    explanation="这是当前采样窗口的观察结果，不足以单独证明长期性能问题。",
                    confidence=OptimizationConfidence.MEDIUM,
                    evidence_types=(OptimizationEvidence.MULTI_SAMPLE_COUNTER,),
                    evidence={
                        "average_percent": round(cpu.average_percent, 2),
                        "peak_percent": round(cpu.peak_percent, 2),
                        "sample_count": len(cpu.samples),
                    },
                    limitations=("短时采样可能受当前任务影响",),
                )
            )
        memory = snapshot.memory
        if (
            memory is not None
            and memory.used_percent >= 85
            and memory.available_bytes <= max(2 * 1024**3, memory.total_bytes // 10)
        ):
            evidence: dict[str, int | float | None] = {
                "used_percent": round(memory.used_percent, 2),
                "available_bytes": memory.available_bytes,
                "pagefile_used_percent": memory.pagefile_used_percent,
            }
            findings.append(
                PerformanceFinding(
                    category=PerformanceCategory.MEMORY_PRESSURE,
                    title="内存使用率和绝对可用量同时达到关注条件",
                    explanation="缓存和正在进行的工作仍可能合理占用内存，建议结合进程采样复查。",
                    confidence=OptimizationConfidence.MEDIUM,
                    evidence_types=(OptimizationEvidence.WINDOWS_QUERY_API,),
                    evidence=evidence,
                    limitations=("当前平台未必提供完整 Commit 计数器",),
                )
            )
        for disk in snapshot.disks:
            if disk.used_percent >= 85 and disk.free_bytes <= 20 * 1024**3:
                findings.append(
                    PerformanceFinding(
                        category=PerformanceCategory.DISK_SPACE_PRESSURE,
                        title="本地磁盘的比例和绝对剩余空间都偏低",
                        explanation="低可用空间可能影响更新和临时工作，但本阶段不会清理文件。",
                        confidence=OptimizationConfidence.HIGH,
                        evidence_types=(OptimizationEvidence.WINDOWS_QUERY_API,),
                        evidence={
                            "mountpoint": str(disk.mountpoint),
                            "used_percent": round(disk.used_percent, 2),
                            "free_bytes": disk.free_bytes,
                        },
                    )
                )
        processes = snapshot.processes
        if processes is not None:
            hotspots = tuple(
                item
                for item in processes.processes
                if item.cpu_percent >= 25 or item.memory_percent >= 10
            )
            if hotspots:
                top = sorted(
                    hotspots,
                    key=lambda item: (item.cpu_percent, item.memory_rss_bytes),
                    reverse=True,
                )[:10]
                findings.append(
                    PerformanceFinding(
                        category=PerformanceCategory.BACKGROUND_PROCESS_LOAD,
                        title="部分进程在当前采样中资源占用较高",
                        explanation="活动应用可能正常达到这些数值；该结果只用于复查，不会终止进程。",
                        confidence=OptimizationConfidence.MEDIUM,
                        evidence_types=(OptimizationEvidence.MULTI_SAMPLE_COUNTER,),
                        evidence={
                            "matching_count": len(hotspots),
                            "top": [
                                {
                                    "pid": item.pid,
                                    "name": item.name,
                                    "cpu_percent": round(item.cpu_percent, 2),
                                    "memory_percent": round(item.memory_percent, 2),
                                }
                                for item in top
                            ],
                        },
                        limitations=("进程采样不代表长期平均负载",),
                    )
                )
        startup_count = len(snapshot.startup_entries)
        if startup_count >= 20:
            findings.append(
                PerformanceFinding(
                    category=PerformanceCategory.STARTUP_LOAD,
                    title="检测到较多启动项",
                    explanation="数量本身不能证明启动缓慢，其中可能包含安全和更新组件。",
                    confidence=OptimizationConfidence.LOW,
                    evidence_types=(OptimizationEvidence.WINDOWS_QUERY_API,),
                    evidence={"entry_count": startup_count},
                    limitations=("未采集每个启动项的实际启动耗时",),
                )
            )
        if not findings:
            findings.append(
                PerformanceFinding(
                    category=PerformanceCategory.NO_CLEAR_BOTTLENECK,
                    title="当前短时观察没有发现明确瓶颈",
                    explanation="这不表示电脑始终没有问题；间歇性问题需要在发生时重新采样。",
                    confidence=OptimizationConfidence.MEDIUM,
                    evidence_types=(OptimizationEvidence.MULTI_SAMPLE_COUNTER,),
                    evidence={"available_collector_count": len(snapshot.outcomes)},
                    limitations=("结果仅代表当前采样窗口",),
                )
            )
        return tuple(findings)
