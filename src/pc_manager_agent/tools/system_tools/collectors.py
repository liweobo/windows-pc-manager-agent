"""Registered R0 tools that delegate to a query-only platform adapter."""

from __future__ import annotations

from pydantic import BaseModel

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.system_diagnostics import (
    CpuCollectorRequest,
    CpuResult,
    DiskResult,
    EmptyCollectorRequest,
    LimitedCollectorRequest,
    MemoryResult,
    ProcessCollectorRequest,
    ProcessResult,
    ServiceResult,
    SoftwareResult,
    StartupResult,
    SystemInfoResult,
)
from pc_manager_agent.platform_support.base import SystemDiagnosticsPlatform
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


def _manifest(
    *,
    name: str,
    description: str,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
    supports_cancellation: bool = False,
    max_batch_size: int = 5_000,
) -> ToolManifest:
    """Build one complete immutable R0 manifest with shared safety guarantees."""
    return ToolManifest(
        name=name,
        description=description,
        input_model=input_model,
        output_model=output_model,
        risk_level=RiskLevel.R0,
        required_permissions=("current-user-query",),
        read_only=True,
        idempotent=False,
        supports_cancellation=supports_cancellation,
        rollback_level=RollbackLevel.NONE,
        preconditions=("plan is confirmed", "audit store is available", "Windows is running"),
        postconditions=("no process, service, registry, startup, or software state is modified",),
        timeout_seconds=30.0,
        max_batch_size=max_batch_size,
        audit_fields=("collector", "bounded parameters", "outcome counts", "duration"),
        supported_platforms=("windows",),
    )


class SystemInfoTool:
    """Collect operating-system and hardware identity metadata."""

    def __init__(self, platform: SystemDiagnosticsPlatform) -> None:
        self._platform = platform
        self._manifest = _manifest(
            name="system.info",
            description="Read Windows version, architecture, CPU model, and boot time",
            input_model=EmptyCollectorRequest,
            output_model=SystemInfoResult,
            max_batch_size=1,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 tool manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Validate the request type and collect system information."""
        if not isinstance(request, EmptyCollectorRequest):
            raise TypeError("SystemInfoTool received an unexpected input model")
        return SystemInfoResult(snapshot=self._platform.collect_system_info())


class CpuTool:
    """Collect bounded multi-sample CPU utilization."""

    def __init__(self, platform: SystemDiagnosticsPlatform) -> None:
        self._platform = platform
        self._manifest = _manifest(
            name="system.cpu",
            description="Read several CPU utilization samples and per-core values",
            input_model=CpuCollectorRequest,
            output_model=CpuResult,
            supports_cancellation=True,
            max_batch_size=10,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 tool manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Collect CPU samples using only validated bounded parameters."""
        if not isinstance(request, CpuCollectorRequest):
            raise TypeError("CpuTool received an unexpected input model")
        return CpuResult(
            snapshot=self._platform.collect_cpu(
                request.sample_count, request.interval_seconds, cancellation
            )
        )


class MemoryTool:
    """Collect physical memory and pagefile counters."""

    def __init__(self, platform: SystemDiagnosticsPlatform) -> None:
        self._platform = platform
        self._manifest = _manifest(
            name="system.memory",
            description="Read physical memory and pagefile counters",
            input_model=EmptyCollectorRequest,
            output_model=MemoryResult,
            max_batch_size=1,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 tool manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Collect current memory counters."""
        if not isinstance(request, EmptyCollectorRequest):
            raise TypeError("MemoryTool received an unexpected input model")
        return MemoryResult(snapshot=self._platform.collect_memory())


class DiskTool:
    """Collect local fixed-volume capacity without reading file contents."""

    def __init__(self, platform: SystemDiagnosticsPlatform) -> None:
        self._platform = platform
        self._manifest = _manifest(
            name="system.disks",
            description="Read capacity for mounted local fixed volumes",
            input_model=EmptyCollectorRequest,
            output_model=DiskResult,
            max_batch_size=128,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 tool manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Collect disk capacity and retain recoverable warnings."""
        if not isinstance(request, EmptyCollectorRequest):
            raise TypeError("DiskTool received an unexpected input model")
        snapshots, warnings = self._platform.collect_disks()
        return DiskResult(snapshots=snapshots, warnings=warnings)


class ProcessTool:
    """Collect metadata-only process samples; command lines are never requested."""

    def __init__(self, platform: SystemDiagnosticsPlatform) -> None:
        self._platform = platform
        self._manifest = _manifest(
            name="system.processes",
            description="Read bounded process identity and resource metadata without command lines",
            input_model=ProcessCollectorRequest,
            output_model=ProcessResult,
            supports_cancellation=True,
            max_batch_size=2_000,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 tool manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Collect bounded process metadata and protected-process statistics."""
        if not isinstance(request, ProcessCollectorRequest):
            raise TypeError("ProcessTool received an unexpected input model")
        collection, warnings = self._platform.collect_processes(
            request.sample_interval_seconds,
            request.max_processes,
            cancellation,
        )
        return ProcessResult(collection=collection, warnings=warnings)


class StartupTool:
    """Read startup registry values and Startup-folder entries."""

    def __init__(self, platform: SystemDiagnosticsPlatform) -> None:
        self._platform = platform
        self._manifest = _manifest(
            name="system.startup",
            description="Read startup entries from Run keys and Startup folders",
            input_model=LimitedCollectorRequest,
            output_model=StartupResult,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 tool manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Enumerate bounded startup metadata without resolving or executing entries."""
        if not isinstance(request, LimitedCollectorRequest):
            raise TypeError("StartupTool received an unexpected input model")
        entries, warnings, truncated = self._platform.collect_startup(request.max_items)
        return StartupResult(entries=entries, warnings=warnings, truncated=truncated)


class ServiceTool:
    """Query service state and configuration with read-only SCM handles."""

    def __init__(self, platform: SystemDiagnosticsPlatform) -> None:
        self._platform = platform
        self._manifest = _manifest(
            name="system.services",
            description="Read Windows service state and query-only configuration",
            input_model=LimitedCollectorRequest,
            output_model=ServiceResult,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 tool manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Enumerate bounded service metadata without service controls."""
        if not isinstance(request, LimitedCollectorRequest):
            raise TypeError("ServiceTool received an unexpected input model")
        services, warnings, truncated = self._platform.collect_services(request.max_items)
        return ServiceResult(services=services, warnings=warnings, truncated=truncated)


class SoftwareTool:
    """Read uninstall registry metadata without invoking uninstall commands."""

    def __init__(self, platform: SystemDiagnosticsPlatform) -> None:
        self._platform = platform
        self._manifest = _manifest(
            name="system.software",
            description="Read and deduplicate installed-software registry metadata",
            input_model=LimitedCollectorRequest,
            output_model=SoftwareResult,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 tool manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Enumerate bounded software records and omit uninstall commands."""
        if not isinstance(request, LimitedCollectorRequest):
            raise TypeError("SoftwareTool received an unexpected input model")
        software, warnings, truncated = self._platform.collect_software(request.max_items)
        return SoftwareResult(software=software, warnings=warnings, truncated=truncated)
