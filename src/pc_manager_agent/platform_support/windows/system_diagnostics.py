"""Windows query-only implementation of the Stage 3 diagnostics platform."""

from __future__ import annotations

import ctypes
import os
import platform
import socket
import time
import winreg
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import psutil
import pywintypes
import win32service

from pc_manager_agent.domain.system_diagnostics import (
    AccessCompleteness,
    CpuSample,
    CpuSnapshot,
    DiskKind,
    DiskSnapshot,
    InstalledSoftware,
    MemorySnapshot,
    ProcessCollection,
    ProcessGroupSnapshot,
    ProcessSnapshot,
    ServiceSnapshot,
    SoftwareArchitecture,
    SoftwareScope,
    StartupEntry,
    StartupSource,
    SystemInfoSnapshot,
)
from pc_manager_agent.platform_support.base import CancellationSignal

_CURRENT_VERSION_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
_PROCESSOR_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
_UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
_STARTUP_KEY_SOURCES = (
    (
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        StartupSource.HKCU_RUN,
        SoftwareScope.CURRENT_USER,
    ),
    (
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
        StartupSource.HKCU_RUN_ONCE,
        SoftwareScope.CURRENT_USER,
    ),
    (
        winreg.HKEY_LOCAL_MACHINE,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        StartupSource.HKLM_RUN,
        SoftwareScope.LOCAL_MACHINE,
    ),
    (
        winreg.HKEY_LOCAL_MACHINE,
        r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
        StartupSource.HKLM_RUN_ONCE,
        SoftwareScope.LOCAL_MACHINE,
    ),
)


class DiagnosticCollectionCancelled(RuntimeError):
    """Raised when a bounded sampling operation observes cancellation."""


def _utc_from_timestamp(value: float) -> datetime:
    """Convert a platform timestamp to an aware UTC datetime."""
    return datetime.fromtimestamp(value, tz=UTC)


def _wait_with_cancellation(seconds: float, cancellation: CancellationSignal) -> None:
    """Wait in short slices so cancellation remains responsive."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if cancellation.cancellation_requested():
            raise DiagnosticCollectionCancelled("System diagnostic collection was cancelled")
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


def _read_registry_value(
    hive: int,
    key_path: str,
    value_name: str,
    *,
    access: int = winreg.KEY_READ,
) -> str | None:
    """Read one string-like registry value and fail softly when unavailable."""
    try:
        with winreg.OpenKey(hive, key_path, 0, access) as key:
            value, _kind = winreg.QueryValueEx(key, value_name)
    except OSError:
        return None
    return str(value).strip() or None


def _drive_kind(mountpoint: str) -> DiskKind:
    """Map GetDriveTypeW to a finite, cross-layer disk classification."""
    code = ctypes.windll.kernel32.GetDriveTypeW(mountpoint)
    return {
        2: DiskKind.REMOVABLE,
        3: DiskKind.FIXED,
        4: DiskKind.NETWORK,
        5: DiskKind.OPTICAL,
        6: DiskKind.RAMDISK,
    }.get(code, DiskKind.UNKNOWN)


def _service_state(value: int) -> str:
    """Return a stable label for a Service Control Manager state constant."""
    return {
        win32service.SERVICE_STOPPED: "stopped",
        win32service.SERVICE_START_PENDING: "start_pending",
        win32service.SERVICE_STOP_PENDING: "stop_pending",
        win32service.SERVICE_RUNNING: "running",
        win32service.SERVICE_CONTINUE_PENDING: "continue_pending",
        win32service.SERVICE_PAUSE_PENDING: "pause_pending",
        win32service.SERVICE_PAUSED: "paused",
    }.get(value, "unknown")


def _service_start_type(value: int) -> str:
    """Return a stable label for a service start-type constant."""
    return {
        win32service.SERVICE_AUTO_START: "automatic",
        win32service.SERVICE_BOOT_START: "boot",
        win32service.SERVICE_DEMAND_START: "manual",
        win32service.SERVICE_DISABLED: "disabled",
        win32service.SERVICE_SYSTEM_START: "system",
    }.get(value, "unknown")


def _extract_executable_path(binary_path: str) -> Path | None:
    """Extract only the executable portion of a service image path, discarding arguments."""
    value = os.path.expandvars(binary_path.strip())
    if not value:
        return None
    if value.startswith('"'):
        closing = value.find('"', 1)
        candidate = value[1:closing] if closing > 1 else ""
    else:
        lowered = value.lower()
        executable_end = lowered.find(".exe")
        candidate = value[: executable_end + 4] if executable_end >= 0 else value.split()[0]
    return Path(candidate) if candidate else None


class WindowsSystemDiagnosticsPlatform:
    """Collect bounded, query-only Windows state without elevation or subprocesses."""

    def collect_system_info(self) -> SystemInfoSnapshot:
        """Read OS, CPU, architecture and boot metadata from stable local APIs."""
        release = (
            _read_registry_value(winreg.HKEY_LOCAL_MACHINE, _CURRENT_VERSION_KEY, "DisplayVersion")
            or platform.release()
        )
        build = (
            _read_registry_value(
                winreg.HKEY_LOCAL_MACHINE, _CURRENT_VERSION_KEY, "CurrentBuildNumber"
            )
            or platform.version()
        )
        ubr = _read_registry_value(winreg.HKEY_LOCAL_MACHINE, _CURRENT_VERSION_KEY, "UBR")
        if ubr:
            build = f"{build}.{ubr}"
        boot_time = _utc_from_timestamp(psutil.boot_time())
        return SystemInfoSnapshot(
            computer_name=socket.gethostname(),
            windows_edition=_read_registry_value(
                winreg.HKEY_LOCAL_MACHINE, _CURRENT_VERSION_KEY, "ProductName"
            ),
            windows_release=release,
            windows_build=build,
            architecture=platform.machine() or "unknown",
            processor_model=_read_registry_value(
                winreg.HKEY_LOCAL_MACHINE, _PROCESSOR_KEY, "ProcessorNameString"
            ),
            installed_ram_bytes=psutil.virtual_memory().total,
            boot_time=boot_time,
            uptime_seconds=max(0, int((datetime.now(UTC) - boot_time).total_seconds())),
        )

    def collect_cpu(
        self,
        sample_count: int,
        interval_seconds: float,
        cancellation: CancellationSignal,
    ) -> CpuSnapshot:
        """Measure CPU several times with cancellable intervals."""
        psutil.cpu_percent(interval=None, percpu=True)
        samples: list[CpuSample] = []
        for _index in range(sample_count):
            _wait_with_cancellation(interval_seconds, cancellation)
            per_core = tuple(float(item) for item in psutil.cpu_percent(interval=None, percpu=True))
            total = sum(per_core) / len(per_core) if per_core else 0.0
            samples.append(CpuSample(total_percent=total, per_core_percent=per_core))
        totals = tuple(item.total_percent for item in samples)
        frequency = psutil.cpu_freq()
        logical = psutil.cpu_count(logical=True) or max(len(samples[-1].per_core_percent), 1)
        return CpuSnapshot(
            samples=tuple(samples),
            average_percent=sum(totals) / len(totals),
            peak_percent=max(totals),
            physical_cores=psutil.cpu_count(logical=False),
            logical_cores=logical,
            current_frequency_mhz=frequency.current if frequency is not None else None,
        )

    def collect_memory(self) -> MemorySnapshot:
        """Return virtual-memory and swap/pagefile counters without allocation."""
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return MemorySnapshot(
            total_bytes=memory.total,
            available_bytes=memory.available,
            used_bytes=memory.used,
            used_percent=memory.percent,
            cached_bytes=getattr(memory, "cached", None),
            pagefile_total_bytes=swap.total,
            pagefile_used_bytes=swap.used,
            pagefile_used_percent=swap.percent,
            commit_limit_bytes=None,
            commit_used_bytes=None,
        )

    def collect_disks(self) -> tuple[tuple[DiskSnapshot, ...], tuple[str, ...]]:
        """Read only local fixed-volume capacity and report inaccessible volumes."""
        snapshots: list[DiskSnapshot] = []
        warnings: list[str] = []
        for partition in psutil.disk_partitions(all=False):
            kind = _drive_kind(partition.mountpoint)
            if kind is not DiskKind.FIXED:
                continue
            try:
                usage = psutil.disk_usage(partition.mountpoint)
            except (OSError, PermissionError) as exc:
                warnings.append(f"Could not read disk {partition.mountpoint}: {type(exc).__name__}")
                continue
            snapshots.append(
                DiskSnapshot(
                    device=partition.device,
                    mountpoint=Path(partition.mountpoint),
                    filesystem=partition.fstype or None,
                    kind=kind,
                    total_bytes=usage.total,
                    used_bytes=usage.used,
                    free_bytes=usage.free,
                    used_percent=usage.percent,
                )
            )
        return tuple(snapshots), tuple(warnings)

    def collect_processes(
        self,
        interval_seconds: float,
        max_processes: int,
        cancellation: CancellationSignal,
    ) -> tuple[ProcessCollection, tuple[str, ...]]:
        """Sample processes without requesting or retaining command-line arguments."""
        processes = list(psutil.process_iter())
        truncated = len(processes) > max_processes
        processes = processes[:max_processes]
        for process in processes:
            try:
                process.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        _wait_with_cancellation(interval_seconds, cancellation)
        rows: list[ProcessSnapshot] = []
        skipped = 0
        warnings: list[str] = []
        for process in processes:
            if cancellation.cancellation_requested():
                raise DiagnosticCollectionCancelled("System diagnostic collection was cancelled")
            partial = False
            try:
                with process.oneshot():
                    name = process.name() or f"PID {process.pid}"
                    try:
                        executable_value = process.exe()
                        executable = Path(executable_value) if executable_value else None
                    except (psutil.AccessDenied, psutil.NoSuchProcess):
                        executable = None
                        partial = True
                    try:
                        username = process.username()
                    except (psutil.AccessDenied, psutil.NoSuchProcess):
                        username = None
                        partial = True
                    try:
                        started = _utc_from_timestamp(process.create_time())
                    except (psutil.AccessDenied, psutil.NoSuchProcess, ValueError):
                        started = None
                        partial = True
                    row = ProcessSnapshot(
                        pid=process.pid,
                        name=name,
                        executable_path=executable,
                        username=username,
                        status=process.status(),
                        started_at=started,
                        cpu_percent=max(process.cpu_percent(interval=None), 0.0),
                        memory_rss_bytes=process.memory_info().rss,
                        memory_percent=max(process.memory_percent(), 0.0),
                        thread_count=process.num_threads(),
                        parent_pid=process.ppid(),
                        access=(
                            AccessCompleteness.PARTIAL if partial else AccessCompleteness.COMPLETE
                        ),
                    )
            except psutil.NoSuchProcess:
                skipped += 1
                continue
            except psutil.AccessDenied:
                skipped += 1
                continue
            rows.append(row)
        groups: dict[tuple[str, str], list[ProcessSnapshot]] = defaultdict(list)
        for row in rows:
            # Same names at different executable paths are not assumed to be the same app.
            # Incomplete rows stay as single-process groups because identity is insufficient.
            path_key = (
                os.path.normcase(str(row.executable_path))
                if row.executable_path is not None and row.access is AccessCompleteness.COMPLETE
                else f"pid:{row.pid}"
            )
            groups[(row.name.casefold(), path_key)].append(row)
        grouped = tuple(
            ProcessGroupSnapshot(
                normalized_name=name_and_path[0],
                process_count=len(items),
                total_cpu_percent=sum(item.cpu_percent for item in items),
                total_memory_rss_bytes=sum(item.memory_rss_bytes for item in items),
                pids=tuple(sorted(item.pid for item in items)),
            )
            for name_and_path, items in sorted(groups.items())
        )
        partial_count = sum(row.access is AccessCompleteness.PARTIAL for row in rows)
        if skipped:
            warnings.append(f"Skipped {skipped} exited or protected processes")
        if truncated:
            warnings.append(f"Process list was limited to {max_processes} entries")
        return (
            ProcessCollection(
                processes=tuple(rows),
                groups=grouped,
                complete_count=len(rows) - partial_count,
                partial_count=partial_count,
                skipped_count=skipped,
                truncated=truncated,
            ),
            tuple(warnings),
        )

    def collect_startup(
        self, max_items: int
    ) -> tuple[tuple[StartupEntry, ...], tuple[str, ...], bool]:
        """Enumerate Run keys and startup folders without resolving shortcuts."""
        entries: list[StartupEntry] = []
        warnings: list[str] = []
        registry_sources: list[
            tuple[int, str, StartupSource, SoftwareScope, int, SoftwareArchitecture]
        ] = []
        for hive, key_path, source, scope in _STARTUP_KEY_SOURCES:
            if hive == winreg.HKEY_LOCAL_MACHINE:
                registry_sources.extend(
                    (
                        (
                            hive,
                            key_path,
                            source,
                            scope,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
                            SoftwareArchitecture.X64,
                        ),
                        (
                            hive,
                            key_path,
                            source,
                            scope,
                            winreg.KEY_READ | winreg.KEY_WOW64_32KEY,
                            SoftwareArchitecture.X86,
                        ),
                    )
                )
            else:
                registry_sources.append(
                    (
                        hive,
                        key_path,
                        source,
                        scope,
                        winreg.KEY_READ,
                        SoftwareArchitecture.NATIVE,
                    )
                )
        seen_entries: set[tuple[str, str, StartupSource, SoftwareArchitecture]] = set()
        for hive, key_path, source, scope, access, architecture in registry_sources:
            try:
                with winreg.OpenKey(hive, key_path, 0, access) as key:
                    value_count = winreg.QueryInfoKey(key)[1]
                    for index in range(value_count):
                        name, value, _kind = winreg.EnumValue(key, index)
                        identity = (name.casefold(), str(value).casefold(), source, architecture)
                        if identity in seen_entries:
                            continue
                        seen_entries.add(identity)
                        entries.append(
                            StartupEntry(
                                name=name or "(Default)",
                                source=source,
                                scope=scope,
                                architecture=architecture,
                                command_or_path=str(value),
                            )
                        )
                        if len(entries) >= max_items:
                            return tuple(entries), tuple(warnings), True
            except OSError as exc:
                warnings.append(
                    f"Could not read startup source {source.value}: {type(exc).__name__}"
                )
        folders = (
            (os.getenv("APPDATA"), StartupSource.USER_STARTUP_FOLDER, SoftwareScope.CURRENT_USER),
            (
                os.getenv("PROGRAMDATA"),
                StartupSource.COMMON_STARTUP_FOLDER,
                SoftwareScope.LOCAL_MACHINE,
            ),
        )
        suffix = Path("Microsoft/Windows/Start Menu/Programs/Startup")
        for base, source, scope in folders:
            if not base:
                continue
            folder = Path(base) / suffix
            try:
                for path in folder.iterdir():
                    entries.append(
                        StartupEntry(
                            name=path.name,
                            source=source,
                            scope=scope,
                            architecture=SoftwareArchitecture.NATIVE,
                            command_or_path=str(path),
                        )
                    )
                    if len(entries) >= max_items:
                        return tuple(entries), tuple(warnings), True
            except FileNotFoundError:
                continue
            except OSError as exc:
                warnings.append(
                    f"Could not read startup source {source.value}: {type(exc).__name__}"
                )
        return tuple(entries), tuple(warnings), False

    def collect_services(
        self, max_items: int
    ) -> tuple[tuple[ServiceSnapshot, ...], tuple[str, ...], bool]:
        """Query SCM enumeration and configuration handles with query-only access."""
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ENUMERATE_SERVICE)
        services: list[ServiceSnapshot] = []
        warnings: list[str] = []
        try:
            statuses = win32service.EnumServicesStatus(
                scm, win32service.SERVICE_WIN32, win32service.SERVICE_STATE_ALL
            )
            truncated = len(statuses) > max_items
            for service_name, display_name, status in statuses[:max_items]:
                start_type: str | None = None
                account: str | None = None
                executable: Path | None = None
                handle = None
                try:
                    handle = win32service.OpenService(
                        scm, service_name, win32service.SERVICE_QUERY_CONFIG
                    )
                    config = win32service.QueryServiceConfig(handle)
                    start_type = _service_start_type(int(config[1]))
                    executable = _extract_executable_path(str(config[3]))
                    account = str(config[7]) if config[7] else None
                    description = None
                    try:
                        description_config = win32service.QueryServiceConfig2(
                            handle, win32service.SERVICE_CONFIG_DESCRIPTION
                        )
                        description = str(description_config) if description_config else None
                    except pywintypes.error:
                        description = None
                except pywintypes.error as exc:
                    warnings.append(
                        f"Could not query service configuration {service_name}: "
                        f"WinError {exc.winerror}"
                    )
                finally:
                    if handle is not None:
                        win32service.CloseServiceHandle(handle)
                services.append(
                    ServiceSnapshot(
                        name=service_name,
                        display_name=display_name,
                        state=_service_state(int(status[1])),
                        start_type=start_type,
                        account=account,
                        executable_path=executable,
                        description=description,
                    )
                )
        finally:
            win32service.CloseServiceHandle(scm)
        return tuple(services), tuple(warnings), truncated

    def collect_software(
        self, max_items: int
    ) -> tuple[tuple[InstalledSoftware, ...], tuple[str, ...], bool]:
        """Enumerate uninstall registry metadata and discard uninstall commands."""
        records: list[InstalledSoftware] = []
        warnings: list[str] = []
        locations = (
            (
                winreg.HKEY_CURRENT_USER,
                SoftwareScope.CURRENT_USER,
                winreg.KEY_READ,
                SoftwareArchitecture.NATIVE,
            ),
            (
                winreg.HKEY_LOCAL_MACHINE,
                SoftwareScope.LOCAL_MACHINE,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
                SoftwareArchitecture.X64,
            ),
            (
                winreg.HKEY_LOCAL_MACHINE,
                SoftwareScope.LOCAL_MACHINE,
                winreg.KEY_READ | winreg.KEY_WOW64_32KEY,
                SoftwareArchitecture.X86,
            ),
        )
        seen: set[tuple[str, str, str, SoftwareScope]] = set()
        truncated = False
        for hive, scope, access, architecture in locations:
            try:
                with winreg.OpenKey(hive, _UNINSTALL_KEY, 0, access) as parent:
                    key_count = winreg.QueryInfoKey(parent)[0]
                    for index in range(key_count):
                        key_name = winreg.EnumKey(parent, index)
                        subkey_path = f"{_UNINSTALL_KEY}\\{key_name}"
                        name = _read_registry_value(hive, subkey_path, "DisplayName", access=access)
                        if not name:
                            continue
                        version = _read_registry_value(
                            hive, subkey_path, "DisplayVersion", access=access
                        )
                        publisher = _read_registry_value(
                            hive, subkey_path, "Publisher", access=access
                        )
                        identity = (
                            name.casefold(),
                            (version or "").casefold(),
                            (publisher or "").casefold(),
                            scope,
                        )
                        if identity in seen:
                            continue
                        seen.add(identity)
                        location = _read_registry_value(
                            hive, subkey_path, "InstallLocation", access=access
                        )
                        estimated_size = _read_registry_value(
                            hive, subkey_path, "EstimatedSize", access=access
                        )
                        try:
                            estimated_size_bytes = (
                                int(estimated_size) * 1024 if estimated_size else None
                            )
                        except ValueError:
                            estimated_size_bytes = None
                        records.append(
                            InstalledSoftware(
                                name=name,
                                version=version,
                                publisher=publisher,
                                install_date=_read_registry_value(
                                    hive, subkey_path, "InstallDate", access=access
                                ),
                                install_location=Path(location) if location else None,
                                estimated_size_bytes=estimated_size_bytes,
                                uninstall_entry_present=True,
                                scope=scope,
                                architecture=architecture,
                                registry_key=subkey_path,
                            )
                        )
                        if len(records) >= max_items:
                            truncated = True
                            break
            except OSError as exc:
                warnings.append(
                    f"Could not read software registry view {architecture.value}: "
                    f"{type(exc).__name__}"
                )
            if truncated:
                break
        records.sort(key=lambda item: (item.name.casefold(), item.version or ""))
        return tuple(records), tuple(warnings), truncated
