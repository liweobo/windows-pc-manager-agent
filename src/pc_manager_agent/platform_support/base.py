"""Cross-platform contracts used by deterministic application services."""

from pathlib import Path
from typing import Protocol

from pc_manager_agent.domain.file_operations import FileState
from pc_manager_agent.domain.system_diagnostics import (
    CpuSnapshot,
    DiskSnapshot,
    InstalledSoftware,
    MemorySnapshot,
    ProcessCollection,
    ServiceSnapshot,
    StartupEntry,
    SystemInfoSnapshot,
)
from pc_manager_agent.domain.trash import RecycleBinCapability, RecycleBinResult


class CancellationSignal(Protocol):
    """Minimal cancellation contract accepted by platform readers."""

    def cancellation_requested(self) -> bool:
        """Return whether the caller requested cooperative cancellation."""
        ...


class SingleInstanceGuard(Protocol):
    """Prevent concurrent instances from owning the same local state."""

    def acquire(self) -> bool:
        """Return true only when this process owns the guard."""
        ...

    def close(self) -> None:
        """Release the guard."""
        ...


class FileOperationPlatform(Protocol):
    """OS adapter for identity-aware, no-overwrite filesystem mutations."""

    def inspect(self, path: Path) -> FileState:
        """Open an existing object without following reparse points and return identity."""
        ...

    def move_same_volume(self, source: Path, destination: Path) -> None:
        """Move one object without copying, deleting, or replacing a destination."""
        ...

    def create_directory(self, destination: Path) -> None:
        """Create exactly one directory and fail if the target already exists."""
        ...

    def remove_empty_directory(self, path: Path) -> None:
        """Remove exactly one empty, already revalidated directory during rollback."""
        ...


class RecycleBinPlatform(Protocol):
    """OS adapter that can only request and verify Windows Recycle Bin placement."""

    def capability(self, path: Path) -> RecycleBinCapability:
        """Return a fail-closed volume and Recycle Bin capability assessment."""
        ...

    def recycle(self, path: Path) -> RecycleBinResult:
        """Move one object to the Recycle Bin without a permanent-delete fallback."""
        ...


class SystemDiagnosticsPlatform(Protocol):
    """Read-only operating-system adapter used by registered Stage 3 tools."""

    def collect_system_info(self) -> SystemInfoSnapshot:
        """Return Windows and hardware identity metadata without mutation."""
        ...

    def collect_cpu(
        self,
        sample_count: int,
        interval_seconds: float,
        cancellation: CancellationSignal,
    ) -> CpuSnapshot:
        """Collect bounded multi-sample CPU utilization."""
        ...

    def collect_memory(self) -> MemorySnapshot:
        """Return physical and virtual memory counters."""
        ...

    def collect_disks(self) -> tuple[tuple[DiskSnapshot, ...], tuple[str, ...]]:
        """Return local fixed-volume capacity and recoverable warnings."""
        ...

    def collect_processes(
        self,
        interval_seconds: float,
        max_processes: int,
        cancellation: CancellationSignal,
    ) -> tuple[ProcessCollection, tuple[str, ...]]:
        """Return metadata-only process samples without command lines."""
        ...

    def collect_startup(
        self, max_items: int
    ) -> tuple[tuple[StartupEntry, ...], tuple[str, ...], bool]:
        """Read startup registry values and startup-folder entries."""
        ...

    def collect_services(
        self, max_items: int
    ) -> tuple[tuple[ServiceSnapshot, ...], tuple[str, ...], bool]:
        """Query Windows Service Control Manager without changing services."""
        ...

    def collect_software(
        self, max_items: int
    ) -> tuple[tuple[InstalledSoftware, ...], tuple[str, ...], bool]:
        """Read uninstall registry records without invoking uninstallers."""
        ...
