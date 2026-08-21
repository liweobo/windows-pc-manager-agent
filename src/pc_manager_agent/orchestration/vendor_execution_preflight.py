"""Read-only process and service preflight for one trusted Vendor uninstaller."""

from __future__ import annotations

import os
from pathlib import Path

from pc_manager_agent.domain.software_uninstall_analysis import (
    NormalizedInstalledSoftware,
    canonical_digest,
)
from pc_manager_agent.domain.vendor_uninstall import (
    VendorExecutionPreflightResult,
    VendorPreflightState,
    VendorRelatedProcess,
    VendorRelatedService,
    VendorUninstallerIdentity,
)
from pc_manager_agent.platform_support.base import SystemDiagnosticsPlatform
from pc_manager_agent.tools.manifest import CancellationToken


class VendorExecutionPreflight:
    """Warn for related apps, block running services, and never control either object."""

    def __init__(self, platform: SystemDiagnosticsPlatform, max_items: int = 5_000) -> None:
        self._platform = platform
        self._max_items = max_items

    def inspect(
        self,
        software: NormalizedInstalledSoftware,
        identity: VendorUninstallerIdentity,
        cancellation: CancellationToken,
        *,
        active_uninstall_present: bool = False,
    ) -> VendorExecutionPreflightResult:
        """Return complete path-correlated observations and conservative blockers."""
        blockers: list[str] = []
        warnings: list[str] = []
        processes: list[VendorRelatedProcess] = []
        services: list[VendorRelatedService] = []
        if software.identity.canonical_digest() != identity.software_identity_hash:
            blockers.append("Trusted Vendor identity no longer matches the software target.")
        if software.install_location is None:
            return VendorExecutionPreflightResult(
                state=VendorPreflightState.UNKNOWN,
                process_probe_complete=False,
                service_probe_complete=False,
                active_uninstall_present=active_uninstall_present,
                blockers=("A known installation location is required for preflight.",),
            )
        root = _canonical(software.install_location)
        process_complete = False
        service_complete = False
        try:
            collection, process_warnings = self._platform.collect_processes(
                0.1,
                min(2_000, self._max_items),
                cancellation,
            )
            warnings.extend(process_warnings)
            process_complete = not process_warnings and not collection.truncated
            for process in collection.processes:
                if process.executable_path is None:
                    continue
                executable = _canonical(process.executable_path)
                if _is_within(executable, root):
                    processes.append(
                        VendorRelatedProcess(
                            pid=process.pid,
                            name=process.name,
                            executable_path_digest=canonical_digest(str(executable)),
                        )
                    )
        except (OSError, RuntimeError, ValueError) as exc:
            warnings.append(f"Process preflight failed: {type(exc).__name__}.")
        try:
            if not cancellation.cancellation_requested():
                observed, service_warnings, truncated = self._platform.collect_services(
                    self._max_items
                )
                warnings.extend(service_warnings)
                service_complete = not service_warnings and not truncated
                for service in observed:
                    if service.executable_path is None:
                        continue
                    executable = _canonical(service.executable_path)
                    if _is_within(executable, root):
                        services.append(
                            VendorRelatedService(
                                service_name=service.name,
                                display_name=service.display_name,
                                state=service.state,
                                executable_path_digest=canonical_digest(str(executable)),
                            )
                        )
        except (OSError, RuntimeError, ValueError) as exc:
            warnings.append(f"Service preflight failed: {type(exc).__name__}.")
        if cancellation.cancellation_requested():
            blockers.append("Preflight was cancelled before completion.")
        if processes:
            warnings.append(
                "Related applications are running. Close them first when practical; the Agent "
                "will not terminate them, and the Vendor UI controls its own interaction."
            )
        if any(service.state.strip().casefold() == "running" for service in services):
            blockers.append(
                "A related service is running; the Agent will not stop it automatically."
            )
        if not process_complete:
            blockers.append("Process evidence is incomplete.")
        if not service_complete:
            blockers.append("Service evidence is incomplete.")
        if active_uninstall_present:
            blockers.append("Another MSI or Vendor uninstall transaction is active.")
        return VendorExecutionPreflightResult(
            state=VendorPreflightState.READY if not blockers else VendorPreflightState.BLOCKED,
            related_processes=tuple(processes),
            related_services=tuple(services),
            process_probe_complete=process_complete,
            service_probe_complete=service_complete,
            active_uninstall_present=active_uninstall_present,
            blockers=tuple(blockers),
            warnings=tuple(warnings),
        )


def _canonical(path: Path) -> Path:
    """Normalize path text without resolving links into an unapproved location."""
    return Path(os.path.normcase(os.path.abspath(os.fspath(path))))


def _is_within(path: Path, root: Path) -> bool:
    """Return true only for a path component descendant of one exact root."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
