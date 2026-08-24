"""Read-only process/service correlation for one exact MSIX install root."""

from __future__ import annotations

from pathlib import Path

from pc_manager_agent.domain.msix_uninstall import (
    MsixPreflight,
    MsixPreflightState,
    NormalizedMsixPackage,
)
from pc_manager_agent.platform_support.windows.system_diagnostics import (
    WindowsSystemDiagnosticsPlatform,
)
from pc_manager_agent.tools.manifest import CancellationToken


class MsixExecutionPreflightService:
    """Correlate installed-path evidence without terminating processes or stopping services."""

    def __init__(self, platform: WindowsSystemDiagnosticsPlatform, max_items: int = 5_000) -> None:
        self._platform = platform
        self._max_items = max_items

    def inspect(
        self, package: NormalizedMsixPackage, another_uninstall_active: bool
    ) -> MsixPreflight:
        """Return complete warning/blocker counts for the package's exact install root."""
        if not package.installed_path:
            return MsixPreflight(
                state=MsixPreflightState.UNKNOWN,
                process_probe_complete=False,
                service_probe_complete=False,
                related_process_count=0,
                related_service_count=0,
                another_uninstall_active=another_uninstall_active,
                blockers=("Package install path is unavailable for lifecycle correlation.",),
            )
        try:
            root = Path(package.installed_path).resolve(strict=True)
            process_collection, process_warnings = self._platform.collect_processes(
                0.0,
                self._max_items,
                CancellationToken(),
            )
            services, service_warnings, service_truncated = self._platform.collect_services(
                self._max_items
            )
        except OSError as exc:
            return MsixPreflight(
                state=MsixPreflightState.UNKNOWN,
                process_probe_complete=False,
                service_probe_complete=False,
                related_process_count=0,
                related_service_count=0,
                another_uninstall_active=another_uninstall_active,
                blockers=(f"MSIX lifecycle preflight failed: {type(exc).__name__}.",),
            )
        related_processes = tuple(
            process
            for process in process_collection.processes
            if process.executable_path and _inside(process.executable_path, root)
        )
        related_services = tuple(
            service
            for service in services
            if service.executable_path and _inside(service.executable_path, root)
        )
        running_services = tuple(
            service for service in related_services if service.state.casefold() == "running"
        )
        complete_process = not process_collection.truncated and not process_warnings
        complete_service = not service_truncated and not service_warnings
        blockers: list[str] = []
        if not complete_process or not complete_service:
            blockers.append("Process or service evidence is incomplete.")
        if running_services:
            blockers.append("A related Windows service is running; the Agent will not stop it.")
        if another_uninstall_active:
            blockers.append("Another software uninstall transaction is active.")
        warnings = (
            (f"{len(related_processes)} related app process(es) are running; none will be closed.",)
            if related_processes
            else ()
        )
        return MsixPreflight(
            state=MsixPreflightState.BLOCKED if blockers else MsixPreflightState.READY,
            process_probe_complete=complete_process,
            service_probe_complete=complete_service,
            related_process_count=len(related_processes),
            related_service_count=len(related_services),
            another_uninstall_active=another_uninstall_active,
            blockers=tuple(blockers),
            warnings=warnings,
        )


def _inside(path: Path, root: Path) -> bool:
    """Return true only when one resolved local path stays under the exact package root."""
    try:
        path.resolve(strict=True).relative_to(root)
    except (OSError, ValueError):
        return False
    return True
