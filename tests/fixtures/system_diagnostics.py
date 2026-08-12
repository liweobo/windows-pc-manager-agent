"""Deterministic fake platform and registry for Stage 3 tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

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
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.collectors import (
    CpuTool,
    DiskTool,
    MemoryTool,
    ProcessTool,
    ServiceTool,
    SoftwareTool,
    StartupTool,
    SystemInfoTool,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FakeSystemPlatform:
    """Return fixed read-only observations and optionally fail CPU collection."""

    def __init__(self, *, fail_cpu: bool = False) -> None:
        self.fail_cpu = fail_cpu
        self.cpu_call: tuple[int, float] | None = None
        self.process_call: tuple[float, int] | None = None

    def collect_system_info(self) -> SystemInfoSnapshot:
        return SystemInfoSnapshot(
            computer_name="TEST-PC",
            windows_edition="Windows 11 Pro",
            windows_release="24H2",
            windows_build="26100.1",
            architecture="AMD64",
            processor_model="Test CPU",
            installed_ram_bytes=16 * 1024**3,
            boot_time=NOW,
            uptime_seconds=3600,
        )

    def collect_cpu(
        self,
        sample_count: int,
        interval_seconds: float,
        cancellation: CancellationSignal,
    ) -> CpuSnapshot:
        self.cpu_call = (sample_count, interval_seconds)
        if self.fail_cpu:
            raise PermissionError("synthetic protected counter")
        samples = tuple(
            CpuSample(captured_at=NOW, total_percent=value, per_core_percent=(value, value))
            for value in (80.0, 90.0, 85.0)[:sample_count]
        )
        if len(samples) < sample_count:
            samples += tuple(samples[-1] for _index in range(sample_count - len(samples)))
        return CpuSnapshot(
            samples=samples,
            average_percent=sum(item.total_percent for item in samples) / len(samples),
            peak_percent=max(item.total_percent for item in samples),
            physical_cores=1,
            logical_cores=2,
            current_frequency_mhz=3_000,
        )

    def collect_memory(self) -> MemorySnapshot:
        return MemorySnapshot(
            collected_at=NOW,
            total_bytes=100,
            available_bytes=15,
            used_bytes=85,
            used_percent=85,
            pagefile_total_bytes=50,
            pagefile_used_bytes=5,
            pagefile_used_percent=10,
        )

    def collect_disks(self) -> tuple[tuple[DiskSnapshot, ...], tuple[str, ...]]:
        return (
            (
                DiskSnapshot(
                    collected_at=NOW,
                    device="C:",
                    mountpoint=Path("C:/"),
                    filesystem="NTFS",
                    kind=DiskKind.FIXED,
                    total_bytes=100,
                    used_bytes=90,
                    free_bytes=10,
                    used_percent=90,
                ),
            ),
            (),
        )

    def collect_processes(
        self,
        interval_seconds: float,
        max_processes: int,
        cancellation: CancellationSignal,
    ) -> tuple[ProcessCollection, tuple[str, ...]]:
        self.process_call = (interval_seconds, max_processes)
        process = ProcessSnapshot(
            pid=10,
            name="editor.exe",
            executable_path=Path("C:/Apps/editor.exe"),
            username="test-user",
            status="running",
            started_at=NOW,
            cpu_percent=30,
            memory_rss_bytes=20,
            memory_percent=12,
            thread_count=3,
            parent_pid=1,
            access=AccessCompleteness.COMPLETE,
        )
        return (
            ProcessCollection(
                processes=(process,),
                groups=(
                    ProcessGroupSnapshot(
                        normalized_name="editor.exe",
                        process_count=1,
                        total_cpu_percent=30,
                        total_memory_rss_bytes=20,
                        pids=(10,),
                    ),
                ),
                complete_count=1,
                partial_count=0,
                skipped_count=0,
            ),
            (),
        )

    def collect_startup(
        self, max_items: int
    ) -> tuple[tuple[StartupEntry, ...], tuple[str, ...], bool]:
        return (
            (
                StartupEntry(
                    name="Updater",
                    source=StartupSource.HKCU_RUN,
                    scope=SoftwareScope.CURRENT_USER,
                    command_or_path="C:/Apps/updater.exe --background",
                ),
            ),
            (),
            False,
        )

    def collect_services(
        self, max_items: int
    ) -> tuple[tuple[ServiceSnapshot, ...], tuple[str, ...], bool]:
        return (
            (
                ServiceSnapshot(
                    name="ExampleService",
                    display_name="Example Service",
                    state="running",
                    start_type="automatic",
                    account="LocalSystem",
                    executable_path=Path("C:/Apps/service.exe"),
                ),
            ),
            (),
            False,
        )

    def collect_software(
        self, max_items: int
    ) -> tuple[tuple[InstalledSoftware, ...], tuple[str, ...], bool]:
        return (
            (
                InstalledSoftware(
                    name="Example App",
                    version="1.0",
                    publisher="Example Publisher",
                    scope=SoftwareScope.CURRENT_USER,
                    architecture=SoftwareArchitecture.X64,
                    registry_key="SOFTWARE/Example",
                ),
            ),
            (),
            False,
        )


def build_registry(platform: FakeSystemPlatform | None = None) -> ToolRegistry:
    """Register every Stage 3 tool over one deterministic platform fake."""
    adapter = platform or FakeSystemPlatform()
    registry = ToolRegistry()
    for tool in (
        SystemInfoTool(adapter),
        CpuTool(adapter),
        MemoryTool(adapter),
        DiskTool(adapter),
        ProcessTool(adapter),
        StartupTool(adapter),
        ServiceTool(adapter),
        SoftwareTool(adapter),
    ):
        registry.register(tool)
    return registry
