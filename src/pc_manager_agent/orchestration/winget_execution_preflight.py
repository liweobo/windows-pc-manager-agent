"""Read-only process, service, busy-state, and concurrency preflight."""

from __future__ import annotations

import os
from pathlib import Path

from pc_manager_agent.domain.software_uninstall_analysis import (
    NormalizedInstalledSoftware,
    canonical_digest,
)
from pc_manager_agent.domain.winget_uninstall import (
    WingetExecutionPreflight,
    WingetPreflightState,
    WingetRelatedProcess,
    WingetRelatedService,
)
from pc_manager_agent.platform_support.base import SystemDiagnosticsPlatform
from pc_manager_agent.tools.manifest import CancellationToken


class WingetExecutionPreflightService:
    """Observe related objects but never terminate a process or stop a service."""

    def __init__(self, platform: SystemDiagnosticsPlatform, max_items: int = 5_000) -> None:
        self._platform = platform
        self._max_items = max_items

    def inspect(
        self,
        software: NormalizedInstalledSoftware,
        cancellation: CancellationToken,
        *,
        another_uninstall_active: bool = False,
    ) -> WingetExecutionPreflight:
        """Warn for related apps and block incomplete, busy, or running-service evidence."""
        blockers: list[str] = []
        warnings: list[str] = []
        processes: list[WingetRelatedProcess] = []
        services: list[WingetRelatedService] = []
        process_complete = False
        service_complete = False
        winget_busy = False
        if software.install_location is None:
            return WingetExecutionPreflight(
                state=WingetPreflightState.UNKNOWN,
                process_probe_complete=False,
                service_probe_complete=False,
                winget_busy=False,
                another_uninstall_active=another_uninstall_active,
                blockers=("A known install location is required for exact preflight.",),
            )
        root = _canonical(software.install_location)
        try:
            collection, probe_warnings = self._platform.collect_processes(
                0.1,
                min(2_000, self._max_items),
                cancellation,
            )
            warnings.extend(probe_warnings)
            process_complete = not probe_warnings and not collection.truncated
            for process in collection.processes:
                normalized_name = process.name.strip().casefold()
                if normalized_name in {"winget.exe", "appinstallercli.exe"}:
                    winget_busy = True
                if process.executable_path is None:
                    continue
                executable = _canonical(process.executable_path)
                if _is_within(executable, root):
                    processes.append(
                        WingetRelatedProcess(
                            pid=process.pid,
                            name=process.name,
                            executable_path_digest=canonical_digest(str(executable)),
                        )
                    )
        except (OSError, RuntimeError, ValueError) as exc:
            warnings.append(f"Process preflight failed: {type(exc).__name__}.")
        try:
            if not cancellation.cancellation_requested():
                observed, probe_warnings, truncated = self._platform.collect_services(
                    self._max_items
                )
                warnings.extend(probe_warnings)
                service_complete = not probe_warnings and not truncated
                for service in observed:
                    if service.executable_path is None:
                        continue
                    if _is_within(_canonical(service.executable_path), root):
                        services.append(
                            WingetRelatedService(
                                service_name=service.name,
                                display_name=service.display_name,
                                state=service.state,
                            )
                        )
        except (OSError, RuntimeError, ValueError) as exc:
            warnings.append(f"Service preflight failed: {type(exc).__name__}.")
        if processes:
            warnings.append("Related applications are running; the Agent will not close them.")
        if any(item.state.strip().casefold() == "running" for item in services):
            blockers.append("A related service is running; the Agent will not stop it.")
        if winget_busy:
            blockers.append("Another winget process is active.")
        if another_uninstall_active:
            blockers.append("Another MSI, Vendor, or winget uninstall transaction is active.")
        if cancellation.cancellation_requested():
            blockers.append("Preflight was cancelled.")
        if not process_complete:
            blockers.append("Process evidence is incomplete.")
        if not service_complete:
            blockers.append("Service evidence is incomplete.")
        return WingetExecutionPreflight(
            state=WingetPreflightState.READY if not blockers else WingetPreflightState.BLOCKED,
            related_processes=tuple(processes),
            related_services=tuple(services),
            process_probe_complete=process_complete,
            service_probe_complete=service_complete,
            winget_busy=winget_busy,
            another_uninstall_active=another_uninstall_active,
            blockers=tuple(blockers),
            warnings=tuple(warnings),
        )


def _canonical(path: Path) -> Path:
    return Path(os.path.normcase(os.path.abspath(os.fspath(path))))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
