"""Deterministic, threshold-driven analysis of read-only system snapshots."""

from __future__ import annotations

from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.system_diagnostics import (
    Confidence,
    DiagnosticCategory,
    DiagnosticFinding,
    DiagnosticPlan,
    DiagnosticReport,
    DiagnosticThresholds,
    FindingSeverity,
    SuggestedAction,
    SuggestedActionType,
    SystemSnapshot,
)


def _review_action(title: str, description: str) -> SuggestedAction:
    """Construct a non-executing R0 review suggestion."""
    return SuggestedAction(
        action_type=SuggestedActionType.REVIEW,
        title=title,
        description=description,
        executable=False,
        risk_level=RiskLevel.R0,
    )


class DiagnosticEngine:
    """Turn measured values into conservative findings without claiming root cause."""

    def __init__(self, thresholds: DiagnosticThresholds | None = None) -> None:
        self._thresholds = thresholds or DiagnosticThresholds()

    def analyze(self, plan: DiagnosticPlan, snapshot: SystemSnapshot) -> DiagnosticReport:
        """Evaluate every available category and include actual thresholds in the report."""
        findings: list[DiagnosticFinding] = []
        findings.extend(self._cpu_findings(snapshot))
        findings.extend(self._memory_findings(snapshot))
        findings.extend(self._disk_findings(snapshot))
        findings.extend(self._process_findings(snapshot))
        findings.extend(self._startup_findings(snapshot))
        failed = sum(outcome.state.value == "failed" for outcome in snapshot.outcomes)
        summary = (
            f"Completed {len(snapshot.outcomes)} read-only collectors; "
            f"{failed} failed and {len(findings)} observations met configured thresholds."
        )
        return DiagnosticReport(
            plan_id=plan.plan_id,
            summary=summary,
            snapshot=snapshot,
            findings=tuple(findings),
            thresholds=self._thresholds,
        )

    def _cpu_findings(self, snapshot: SystemSnapshot) -> tuple[DiagnosticFinding, ...]:
        cpu = snapshot.cpu
        if cpu is None or cpu.average_percent < self._thresholds.cpu_notice_percent:
            return ()
        warning = cpu.average_percent >= self._thresholds.cpu_warning_percent
        return (
            DiagnosticFinding(
                code="cpu.sustained-utilization",
                category=DiagnosticCategory.CPU,
                severity=FindingSeverity.WARNING if warning else FindingSeverity.NOTICE,
                confidence=Confidence.MEDIUM,
                title="CPU utilization stayed elevated during the sampling window",
                explanation=(
                    "Several short samples exceeded the configured threshold. This is a "
                    "point-in-time observation and does not identify the cause."
                ),
                evidence={
                    "average_percent": round(cpu.average_percent, 2),
                    "peak_percent": round(cpu.peak_percent, 2),
                    "sample_count": len(cpu.samples),
                },
                threshold={
                    "notice_percent": self._thresholds.cpu_notice_percent,
                    "warning_percent": self._thresholds.cpu_warning_percent,
                },
                actions=(
                    _review_action(
                        "Review process resource usage",
                        "Compare the measured process table and repeat the diagnostic if needed.",
                    ),
                ),
            ),
        )

    def _memory_findings(self, snapshot: SystemSnapshot) -> tuple[DiagnosticFinding, ...]:
        memory = snapshot.memory
        if memory is None or memory.used_percent < self._thresholds.memory_notice_percent:
            return ()
        warning = memory.used_percent >= self._thresholds.memory_warning_percent
        return (
            DiagnosticFinding(
                code="memory.high-utilization",
                category=DiagnosticCategory.MEMORY,
                severity=FindingSeverity.WARNING if warning else FindingSeverity.NOTICE,
                confidence=Confidence.HIGH,
                title="Memory utilization is above the configured threshold",
                explanation=(
                    "The current memory counter is elevated. Cache use and normal workloads can "
                    "change this value, so this does not by itself indicate a fault."
                ),
                evidence={
                    "used_percent": round(memory.used_percent, 2),
                    "available_bytes": memory.available_bytes,
                },
                threshold={
                    "notice_percent": self._thresholds.memory_notice_percent,
                    "warning_percent": self._thresholds.memory_warning_percent,
                },
                actions=(
                    _review_action(
                        "Review high-memory processes",
                        "Use the process table to identify measured memory consumers; "
                        "no process will be stopped.",
                    ),
                ),
            ),
        )

    def _disk_findings(self, snapshot: SystemSnapshot) -> tuple[DiagnosticFinding, ...]:
        findings: list[DiagnosticFinding] = []
        for disk in snapshot.disks:
            if (
                disk.used_percent < self._thresholds.disk_notice_percent
                or disk.free_bytes > self._thresholds.disk_notice_free_bytes
            ):
                continue
            warning = (
                disk.used_percent >= self._thresholds.disk_warning_percent
                and disk.free_bytes <= self._thresholds.disk_warning_free_bytes
            )
            findings.append(
                DiagnosticFinding(
                    code="disk.capacity-utilization",
                    category=DiagnosticCategory.DISK,
                    severity=FindingSeverity.WARNING if warning else FindingSeverity.NOTICE,
                    confidence=Confidence.HIGH,
                    title="A local disk is above the configured capacity threshold",
                    explanation=(
                        "Low free space can affect updates and temporary-file workloads. The "
                        "diagnostic does not delete, move, or select any file."
                    ),
                    evidence={
                        "mountpoint": str(disk.mountpoint),
                        "used_percent": round(disk.used_percent, 2),
                        "free_bytes": disk.free_bytes,
                    },
                    threshold={
                        "notice_percent": self._thresholds.disk_notice_percent,
                        "warning_percent": self._thresholds.disk_warning_percent,
                        "notice_free_bytes": self._thresholds.disk_notice_free_bytes,
                        "warning_free_bytes": self._thresholds.disk_warning_free_bytes,
                    },
                    actions=(
                        _review_action(
                            "Run read-only file analysis",
                            "Review large-file reports before considering any separately "
                            "confirmed file action.",
                        ),
                    ),
                )
            )
        return tuple(findings)

    def _process_findings(self, snapshot: SystemSnapshot) -> tuple[DiagnosticFinding, ...]:
        if snapshot.processes is None:
            return ()
        hotspots = [
            process
            for process in snapshot.processes.processes
            if process.cpu_percent >= self._thresholds.process_cpu_notice_percent
            or process.memory_percent >= self._thresholds.process_memory_notice_percent
        ]
        if not hotspots:
            return ()
        top = sorted(
            hotspots,
            key=lambda item: (item.cpu_percent, item.memory_percent),
            reverse=True,
        )[:10]
        return (
            DiagnosticFinding(
                code="process.resource-observation",
                category=DiagnosticCategory.PROCESS,
                severity=FindingSeverity.NOTICE,
                confidence=Confidence.MEDIUM,
                title="Some processes crossed a resource observation threshold",
                explanation=(
                    "Resource use may be expected for active work. Names and measurements are "
                    "reported for review only; no process is classified as malicious or stopped."
                ),
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
                threshold={
                    "cpu_percent": self._thresholds.process_cpu_notice_percent,
                    "memory_percent": self._thresholds.process_memory_notice_percent,
                },
                actions=(
                    _review_action(
                        "Observe before acting",
                        "Repeat the measurement and verify the application identity in "
                        "Task Manager.",
                    ),
                ),
            ),
        )

    def _startup_findings(self, snapshot: SystemSnapshot) -> tuple[DiagnosticFinding, ...]:
        count = len(snapshot.startup_entries)
        if count < self._thresholds.startup_notice_count:
            return ()
        return (
            DiagnosticFinding(
                code="startup.entry-count",
                category=DiagnosticCategory.STARTUP,
                severity=FindingSeverity.NOTICE,
                confidence=Confidence.LOW,
                title="The observed startup-entry count crossed the review threshold",
                explanation=(
                    "Entry count alone does not prove a startup problem. The list may include "
                    "legitimate update and security software."
                ),
                evidence={"entry_count": count},
                threshold={"notice_count": self._thresholds.startup_notice_count},
                actions=(
                    SuggestedAction(
                        action_type=SuggestedActionType.OPEN_WINDOWS_SETTINGS,
                        title="Review Startup Apps in Windows Settings",
                        description=(
                            "Open Windows Settings yourself and review publishers before "
                            "changing anything."
                        ),
                    ),
                ),
            ),
        )
