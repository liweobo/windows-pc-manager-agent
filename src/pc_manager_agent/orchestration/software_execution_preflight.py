"""Read-only process and service preflight for one validated MSI product."""

from __future__ import annotations

import os
from pathlib import Path

from pc_manager_agent.domain.software_uninstall_analysis import (
    NormalizedInstalledSoftware,
    canonical_digest,
)
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiPreflightState,
    RelatedProcessEvidence,
    RelatedServiceEvidence,
    SoftwareExecutionPreflightResult,
    ValidatedMsiProduct,
)
from pc_manager_agent.platform_support.base import SystemDiagnosticsPlatform
from pc_manager_agent.tools.manifest import CancellationToken


class SoftwareExecutionPreflight:
    """Find only strong path relations and never invoke process/service controls."""

    def __init__(self, platform: SystemDiagnosticsPlatform, max_items: int = 5_000) -> None:
        self._platform = platform
        self._max_items = max_items

    def inspect(
        self,
        software: NormalizedInstalledSoftware,
        product: ValidatedMsiProduct,
        cancellation: CancellationToken,
    ) -> SoftwareExecutionPreflightResult:
        """Return blockers for running related objects or incomplete evidence."""
        blockers: list[str] = []
        warnings: list[str] = []
        related_processes: list[RelatedProcessEvidence] = []
        related_services: list[RelatedServiceEvidence] = []
        if software.identity.canonical_digest() != product.identity_digest:
            blockers.append("Validated MSI identity no longer matches the software target.")
        location = software.install_location
        if location is None:
            blockers.append("A known installation location is required for conservative preflight.")
            return SoftwareExecutionPreflightResult(
                state=MsiPreflightState.UNKNOWN,
                process_probe_complete=False,
                service_probe_complete=False,
                privilege_expected="current_user",
                blockers=tuple(blockers),
                warnings=("No process or service path correlation was possible.",),
            )
        root = _canonical(location)
        process_complete = False
        service_complete = False
        try:
            collection, process_warnings = self._platform.collect_processes(
                0.1,
                min(self._max_items, 2_000),
                cancellation,
            )
            warnings.extend(process_warnings)
            process_complete = not process_warnings and not collection.truncated
            for process in collection.processes:
                if process.executable_path is None:
                    continue
                executable = _canonical(process.executable_path)
                if _is_within(executable, root):
                    related_processes.append(
                        RelatedProcessEvidence(
                            pid=process.pid,
                            name=process.name,
                            executable_path_digest=canonical_digest(str(executable)),
                        )
                    )
        except (OSError, RuntimeError, ValueError) as exc:
            warnings.append(f"Process preflight failed: {type(exc).__name__}.")
        if cancellation.cancellation_requested():
            blockers.append("Preflight was cancelled before completion.")
        try:
            if not cancellation.cancellation_requested():
                services, service_warnings, truncated = self._platform.collect_services(
                    self._max_items
                )
                warnings.extend(service_warnings)
                service_complete = not service_warnings and not truncated
                for service in services:
                    if service.executable_path is None:
                        continue
                    executable = _canonical(service.executable_path)
                    if _is_within(executable, root):
                        related_services.append(
                            RelatedServiceEvidence(
                                service_name=service.name,
                                display_name=service.display_name,
                                state=service.state,
                                executable_path_digest=canonical_digest(str(executable)),
                            )
                        )
        except (OSError, RuntimeError, ValueError) as exc:
            warnings.append(f"Service preflight failed: {type(exc).__name__}.")
        running_services = tuple(
            item for item in related_services if item.state.strip().casefold() == "running"
        )
        if related_processes:
            blockers.append(
                "Related processes are running; close them or use a separate Stage 4A plan."
            )
        if running_services:
            blockers.append(
                "A related service is running; any service action requires a separate "
                "Stage 4C plan."
            )
        if not process_complete:
            blockers.append("Process evidence is incomplete.")
        if not service_complete:
            blockers.append("Service evidence is incomplete.")
        state = MsiPreflightState.READY if not blockers else MsiPreflightState.BLOCKED
        return SoftwareExecutionPreflightResult(
            state=state,
            related_processes=tuple(related_processes),
            related_services=tuple(related_services),
            process_probe_complete=process_complete,
            service_probe_complete=service_complete,
            installer_busy=None,
            reboot_pending=None,
            privilege_expected="current_user",
            blockers=tuple(blockers),
            warnings=tuple(warnings),
        )


def _canonical(path: Path) -> Path:
    """Normalize path text without resolving a junction or symlink target."""
    return Path(os.path.normcase(os.path.abspath(os.fspath(path))))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
