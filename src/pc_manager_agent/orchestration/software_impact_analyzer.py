"""Bounded read-only impact probes for one exact installed-software target."""

from __future__ import annotations

import os
from pathlib import Path

from pc_manager_agent.domain.software_uninstall_analysis import (
    EvidenceKind,
    ImpactSeverity,
    NormalizedInstalledSoftware,
    SoftwareImpactAssessment,
    SoftwareImpactFinding,
    SoftwareSafetyAssessment,
    SoftwareSafetyDecision,
)
from pc_manager_agent.platform_support.base import SystemDiagnosticsPlatform
from pc_manager_agent.tools.manifest import CancellationToken


class SoftwareImpactAnalyzer:
    """Correlate existing R0 process/startup/service metadata without claiming dependencies."""

    def __init__(self, platform: SystemDiagnosticsPlatform, max_items: int = 5_000) -> None:
        self._platform = platform
        self._max_items = max_items

    def analyze(
        self,
        software: NormalizedInstalledSoftware,
        safety: SoftwareSafetyAssessment,
        cancellation: CancellationToken,
    ) -> SoftwareImpactAssessment:
        """Collect partial-aware evidence and always add an honest residual-data warning."""
        findings: list[SoftwareImpactFinding] = []
        warnings: list[str] = []
        if software.estimated_size_bytes is not None:
            findings.append(
                SoftwareImpactFinding(
                    code="estimated-size",
                    kind=EvidenceKind.KNOWN_EVIDENCE,
                    severity=ImpactSeverity.INFO,
                    title="Installer-reported estimated size",
                    explanation=(
                        "This is registry/package metadata, not a measured reclaimable size."
                    ),
                    evidence={"estimated_size_bytes": software.estimated_size_bytes},
                )
            )
        findings.append(
            SoftwareImpactFinding(
                code="scope",
                kind=EvidenceKind.KNOWN_EVIDENCE,
                severity=ImpactSeverity.NOTICE,
                title="Installation scope",
                explanation=(
                    "Machine-scoped software may require elevation in a future separately designed "
                    "stage. Stage 4D1 never requests elevation."
                    if software.scope.value == "local_machine"
                    else "The metadata is scoped to the current user."
                ),
                evidence={"scope": software.scope.value},
            )
        )
        process_complete = startup_complete = service_complete = False
        location = _canonical(software.install_location) if software.install_location else None
        try:
            processes, process_warnings = self._platform.collect_processes(
                0.1, min(2_000, self._max_items), cancellation
            )
            warnings.extend(process_warnings)
            process_complete = not process_warnings and not processes.truncated
            related = tuple(
                item
                for item in processes.processes
                if location is not None
                and item.executable_path is not None
                and _is_within(_canonical(item.executable_path), location)
            )
            if related:
                findings.append(
                    SoftwareImpactFinding(
                        code="running-processes",
                        kind=EvidenceKind.KNOWN_EVIDENCE,
                        severity=ImpactSeverity.WARNING,
                        title="Running processes reference the installation location",
                        explanation="Stage 4D1 will not close or terminate these processes.",
                        evidence={"count": len(related), "pids": [item.pid for item in related]},
                    )
                )
        except (OSError, RuntimeError, ValueError) as exc:
            warnings.append(f"Process impact probe failed: {type(exc).__name__}.")
        if not cancellation.cancellation_requested():
            try:
                startup, startup_warnings, startup_truncated = self._platform.collect_startup(
                    self._max_items
                )
                warnings.extend(startup_warnings)
                startup_complete = not startup_warnings and not startup_truncated
                marker = str(location).replace("\\", "/").casefold() if location is not None else ""
                related_startup = tuple(
                    item
                    for item in startup
                    if marker
                    and marker
                    in os.path.expandvars(item.command_or_path).replace("\\", "/").casefold()
                )
                if related_startup:
                    findings.append(
                        SoftwareImpactFinding(
                            code="startup-references",
                            kind=EvidenceKind.HEURISTIC_WARNING,
                            severity=ImpactSeverity.WARNING,
                            title="Startup metadata may reference this installation",
                            explanation=(
                                "String correlation is not proof of ownership or dependency."
                            ),
                            evidence={"count": len(related_startup)},
                        )
                    )
            except (OSError, RuntimeError, ValueError) as exc:
                warnings.append(f"Startup impact probe failed: {type(exc).__name__}.")
        if not cancellation.cancellation_requested():
            try:
                services, service_warnings, service_truncated = self._platform.collect_services(
                    self._max_items
                )
                warnings.extend(service_warnings)
                service_complete = not service_warnings and not service_truncated
                related_services = tuple(
                    item
                    for item in services
                    if location is not None
                    and item.executable_path is not None
                    and _is_within(_canonical(item.executable_path), location)
                )
                if related_services:
                    findings.append(
                        SoftwareImpactFinding(
                            code="service-references",
                            kind=EvidenceKind.KNOWN_EVIDENCE,
                            severity=ImpactSeverity.WARNING,
                            title="Windows services reference the installation location",
                            explanation=(
                                "Stage 4D1 will not stop, restart, or alter these services."
                            ),
                            evidence={
                                "count": len(related_services),
                                "service_names": [item.name for item in related_services],
                            },
                        )
                    )
            except (OSError, RuntimeError, ValueError) as exc:
                warnings.append(f"Service impact probe failed: {type(exc).__name__}.")
        if safety.decision is SoftwareSafetyDecision.PREVIEW_HIGH_IMPACT:
            findings.append(
                SoftwareImpactFinding(
                    code="high-impact-class",
                    kind=EvidenceKind.HEURISTIC_WARNING,
                    severity=ImpactSeverity.WARNING,
                    title="High-impact software category",
                    explanation="Other applications or system workflows may rely on this category.",
                    evidence={"safety_class": safety.safety_class.value},
                )
            )
        findings.append(
            SoftwareImpactFinding(
                code="residual-user-data",
                kind=EvidenceKind.HEURISTIC_WARNING,
                severity=ImpactSeverity.NOTICE,
                title="User data and settings were not inspected",
                explanation=(
                    "A future vendor uninstall may preserve or remove settings. Stage 4D1 does not "
                    "scan, upload, or delete application data."
                ),
                evidence={},
            )
        )
        if cancellation.cancellation_requested():
            warnings.append("Impact analysis was cancelled and is incomplete.")
        return SoftwareImpactAssessment(
            findings=tuple(findings),
            process_probe_complete=process_complete,
            startup_probe_complete=startup_complete,
            service_probe_complete=service_complete,
            warnings=tuple(warnings),
        )


def _canonical(path: Path) -> Path:
    return Path(os.path.normcase(str(path.resolve(strict=False))))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
