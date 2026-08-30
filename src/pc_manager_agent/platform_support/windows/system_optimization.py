"""Windows metadata-only adapter for Stage 4E1 optimization analysis."""

from __future__ import annotations

import ctypes
import os
import stat
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from pc_manager_agent.domain.system_diagnostics import (
    CollectorError,
    CollectorOutcome,
    CollectorState,
    SystemCollector,
    SystemSnapshot,
)
from pc_manager_agent.domain.system_optimization import (
    CleanupCategory,
    CleanupEvidenceOrigin,
    CleanupSourceReference,
    ObservationAvailability,
    OptimizationEvidence,
    OwnershipConfidence,
    ScanScopeDecision,
    StorageAnalysisResult,
    StorageObservation,
)
from pc_manager_agent.platform_support.base import CancellationSignal
from pc_manager_agent.platform_support.windows.system_diagnostics import (
    DiagnosticCollectionCancelled,
    WindowsSystemDiagnosticsPlatform,
)
from pc_manager_agent.safety.system_cleanup_scope import (
    SystemCleanupScanScopePolicy,
    SystemCleanupScopeError,
)

_S_OK = 0
_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class _SHQueryRBInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


@dataclass(frozen=True, slots=True)
class _KnownRoot:
    source: str
    category: CleanupCategory
    path: Path


@dataclass(slots=True)
class _Budget:
    remaining: int
    deadline: float
    truncated: bool = False

    def consume(self) -> bool:
        if self.remaining <= 0 or time.monotonic() >= self.deadline:
            self.truncated = True
            return False
        self.remaining -= 1
        return True


class WindowsSystemOptimizationPlatform:
    """Compose query-only Windows APIs and a bounded metadata walker."""

    def __init__(self, diagnostics: WindowsSystemDiagnosticsPlatform | None = None) -> None:
        if os.name != "nt":
            raise OSError("Stage 4E1 Windows analysis is available only on Windows")
        self._diagnostics = diagnostics or WindowsSystemDiagnosticsPlatform()
        self._shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    def collect_system_snapshot(
        self,
        *,
        collectors: tuple[SystemCollector, ...],
        sample_count: int,
        sample_interval_seconds: float,
        max_processes: int,
        max_items: int,
        cancellation: CancellationSignal,
    ) -> SystemSnapshot:
        """Collect the Stage 3 query surface while preserving per-source failures."""
        values: dict[SystemCollector, object] = {}
        outcomes: list[CollectorOutcome] = []
        operations: tuple[tuple[SystemCollector, Any], ...] = (
            (SystemCollector.SYSTEM_INFO, self._diagnostics.collect_system_info),
            (
                SystemCollector.CPU,
                lambda: self._diagnostics.collect_cpu(
                    sample_count, sample_interval_seconds, cancellation
                ),
            ),
            (SystemCollector.MEMORY, self._diagnostics.collect_memory),
            (SystemCollector.DISKS, self._diagnostics.collect_disks),
            (
                SystemCollector.PROCESSES,
                lambda: self._diagnostics.collect_processes(
                    sample_interval_seconds, max_processes, cancellation
                ),
            ),
            (SystemCollector.STARTUP, lambda: self._diagnostics.collect_startup(max_items)),
            (SystemCollector.SERVICES, lambda: self._diagnostics.collect_services(max_items)),
            (SystemCollector.SOFTWARE, lambda: self._diagnostics.collect_software(max_items)),
        )
        selected = set(collectors)
        for collector, operation in operations:
            if collector not in selected:
                continue
            started = time.monotonic()
            if cancellation.cancellation_requested():
                outcomes.append(self._cancelled_outcome(collector, started))
                continue
            try:
                value = operation()
                values[collector] = value
                warnings = self._warnings(value)
                outcomes.append(
                    CollectorOutcome(
                        collector=collector,
                        state=CollectorState.PARTIAL if warnings else CollectorState.SUCCEEDED,
                        item_count=self._item_count(collector, value),
                        duration_ms=self._duration_ms(started),
                        warnings=warnings,
                    )
                )
            except DiagnosticCollectionCancelled:
                outcomes.append(self._cancelled_outcome(collector, started))
            except Exception as exc:
                outcomes.append(
                    CollectorOutcome(
                        collector=collector,
                        state=CollectorState.FAILED,
                        duration_ms=self._duration_ms(started),
                        error=CollectorError(
                            code="optimization-source-failed",
                            message=f"{collector.value} unavailable: {type(exc).__name__}",
                        ),
                    )
                )
        disks_value = values.get(SystemCollector.DISKS)
        processes_value = values.get(SystemCollector.PROCESSES)
        startup_value = values.get(SystemCollector.STARTUP)
        services_value = values.get(SystemCollector.SERVICES)
        software_value = values.get(SystemCollector.SOFTWARE)
        return SystemSnapshot(
            system_info=cast(Any, values.get(SystemCollector.SYSTEM_INFO)),
            cpu=cast(Any, values.get(SystemCollector.CPU)),
            memory=cast(Any, values.get(SystemCollector.MEMORY)),
            disks=cast(Any, disks_value[0] if isinstance(disks_value, tuple) else ()),
            processes=cast(Any, processes_value[0] if isinstance(processes_value, tuple) else None),
            startup_entries=cast(Any, startup_value[0] if isinstance(startup_value, tuple) else ()),
            services=cast(Any, services_value[0] if isinstance(services_value, tuple) else ()),
            software=cast(Any, software_value[0] if isinstance(software_value, tuple) else ()),
            outcomes=tuple(outcomes),
        )

    def analyze_storage(
        self,
        *,
        authorized_roots: tuple[Path, ...],
        max_objects: int,
        timeout_seconds: float,
        minimum_large_file_bytes: int,
        inactive_days: int,
        cancellation: CancellationSignal,
    ) -> StorageAnalysisResult:
        """Read metadata under exact allow-listed roots and report unavailable sources."""
        known = self._known_roots()
        policy = SystemCleanupScanScopePolicy(
            known_roots=tuple(item.path for item in known),
            authorized_user_roots=authorized_roots,
            protected_roots=self._protected_roots(),
        )
        budget = _Budget(remaining=max_objects, deadline=time.monotonic() + timeout_seconds)
        observations: list[StorageObservation] = []
        partial: list[str] = []
        skipped: list[str] = []
        for descriptor in known:
            if cancellation.cancellation_requested():
                skipped.append(descriptor.source)
                continue
            if not descriptor.path.exists():
                skipped.append(descriptor.source)
                observations.append(self._missing_observation(descriptor))
                continue
            try:
                root = policy.validate_root(descriptor.path)
                observation = self._scan_aggregate(descriptor, root, policy, budget, cancellation)
            except (OSError, SystemCleanupScopeError) as exc:
                partial.append(descriptor.source)
                observations.append(self._partial_observation(descriptor, exc))
            else:
                observations.append(observation)
                if observation.availability is ObservationAvailability.PARTIAL:
                    partial.append(descriptor.source)
            if budget.truncated:
                break
        if not budget.truncated:
            for root in authorized_roots:
                if cancellation.cancellation_requested():
                    skipped.append("authorized-user-path")
                    break
                try:
                    validated = policy.validate_root(root)
                    observations.extend(
                        self._scan_large_files(
                            validated,
                            policy,
                            budget,
                            cancellation,
                            minimum_large_file_bytes,
                            inactive_days,
                        )
                    )
                except (OSError, SystemCleanupScopeError):
                    partial.append("authorized-user-path")
        observations.extend(self._recycle_bin_observations())
        observations.extend(self._protected_system_observations())
        return StorageAnalysisResult(
            observations=tuple(observations),
            partial_sources=tuple(dict.fromkeys(partial)),
            skipped_sources=tuple(dict.fromkeys(skipped)),
            truncated=budget.truncated,
        )

    @staticmethod
    def _known_roots() -> tuple[_KnownRoot, ...]:
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
        windows = Path(os.environ.get("SYSTEMROOT", "C:/Windows"))
        program_data = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData"))
        return (
            _KnownRoot("current-user-temp", CleanupCategory.USER_TEMP, local / "Temp"),
            _KnownRoot("windows-temp", CleanupCategory.SYSTEM_TEMP, windows / "Temp"),
            _KnownRoot(
                "directx-shader-cache", CleanupCategory.APPLICATION_CACHE, local / "D3DSCache"
            ),
            _KnownRoot(
                "current-user-crash-dumps", CleanupCategory.CRASH_DUMP, local / "CrashDumps"
            ),
            _KnownRoot(
                "windows-error-reports",
                CleanupCategory.LOG,
                program_data / "Microsoft/Windows/WER/ReportArchive",
            ),
            _KnownRoot(
                "edge-default-cache",
                CleanupCategory.BROWSER_CACHE,
                local / "Microsoft/Edge/User Data/Default/Cache/Cache_Data",
            ),
            _KnownRoot(
                "chrome-default-cache",
                CleanupCategory.BROWSER_CACHE,
                local / "Google/Chrome/User Data/Default/Cache/Cache_Data",
            ),
        )

    @staticmethod
    def _protected_roots() -> tuple[Path, ...]:
        windows = Path(os.environ.get("SYSTEMROOT", "C:/Windows"))
        return (windows / "WinSxS", windows / "System32/config", windows / "Installer")

    def _scan_aggregate(
        self,
        descriptor: _KnownRoot,
        root: Path,
        policy: SystemCleanupScanScopePolicy,
        budget: _Budget,
        cancellation: CancellationSignal,
    ) -> StorageObservation:
        size = 0
        count = 0
        oldest: datetime | None = None
        newest: datetime | None = None
        warnings: list[str] = []
        for item in self._walk_metadata(root, policy, budget, cancellation, warnings):
            metadata = item.lstat()
            if stat.S_ISREG(metadata.st_mode):
                count += 1
                size += max(0, int(metadata.st_size))
                modified = datetime.fromtimestamp(metadata.st_mtime, tz=UTC)
                oldest = modified if oldest is None or modified < oldest else oldest
                newest = modified if newest is None or modified > newest else newest
        availability = (
            ObservationAvailability.PARTIAL
            if warnings or budget.truncated
            else ObservationAvailability.AVAILABLE
        )
        evidence = [
            OptimizationEvidence.KNOWN_LOCATION_ALLOWLIST,
            OptimizationEvidence.DIRECT_FILE_METADATA,
        ]
        if warnings or budget.truncated:
            evidence.append(OptimizationEvidence.PARTIAL_ENUMERATION)
        return StorageObservation(
            category=descriptor.category,
            source=descriptor.source,
            path=root,
            observed_size_bytes=size,
            item_count=count,
            oldest_modified_at=oldest,
            newest_modified_at=newest,
            availability=availability,
            scope_decision=ScanScopeDecision.METADATA_ONLY,
            ownership_confidence=OwnershipConfidence.HIGH,
            evidence=tuple(evidence),
            warnings=tuple(warnings[:20]),
            source_reference=CleanupSourceReference(origin=CleanupEvidenceOrigin.KNOWN_LOCATION),
        )

    def _scan_large_files(
        self,
        root: Path,
        policy: SystemCleanupScanScopePolicy,
        budget: _Budget,
        cancellation: CancellationSignal,
        minimum_size: int,
        inactive_days: int,
    ) -> tuple[StorageObservation, ...]:
        observations: list[StorageObservation] = []
        cutoff = datetime.now(UTC) - timedelta(days=inactive_days)
        warnings: list[str] = []
        for item in self._walk_metadata(root, policy, budget, cancellation, warnings):
            try:
                metadata = item.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size < minimum_size:
                continue
            modified = datetime.fromtimestamp(metadata.st_mtime, tz=UTC)
            category = (
                CleanupCategory.INACTIVE_LARGE_FILE
                if modified <= cutoff
                else CleanupCategory.LARGE_FILE
            )
            observations.append(
                StorageObservation(
                    category=category,
                    source="authorized-user-path",
                    path=item,
                    observed_size_bytes=max(0, int(metadata.st_size)),
                    item_count=1,
                    oldest_modified_at=modified,
                    newest_modified_at=modified,
                    availability=ObservationAvailability.AVAILABLE,
                    scope_decision=ScanScopeDecision.AUTHORIZED_USER_PATH,
                    ownership_confidence=OwnershipConfidence.HIGH,
                    evidence=(
                        OptimizationEvidence.AUTHORIZED_STAGE1_SCOPE,
                        OptimizationEvidence.DIRECT_FILE_METADATA,
                    ),
                )
            )
        return tuple(observations)

    def _walk_metadata(
        self,
        root: Path,
        policy: SystemCleanupScanScopePolicy,
        budget: _Budget,
        cancellation: CancellationSignal,
        warnings: list[str],
    ) -> tuple[Path, ...]:
        found: list[Path] = []
        pending = [root]
        while pending:
            if cancellation.cancellation_requested() or not budget.consume():
                break
            current = pending.pop()
            try:
                policy.validate_entry(current, root)
                children = tuple(current.iterdir())
            except (OSError, SystemCleanupScopeError) as exc:
                warnings.append(type(exc).__name__)
                continue
            for child in children:
                if cancellation.cancellation_requested() or not budget.consume():
                    break
                try:
                    policy.validate_entry(child, root)
                    metadata = child.lstat()
                except (OSError, SystemCleanupScopeError) as exc:
                    warnings.append(type(exc).__name__)
                    continue
                attributes = int(getattr(metadata, "st_file_attributes", 0))
                if child.is_symlink() or attributes & _REPARSE_ATTRIBUTE:
                    warnings.append("reparse-skipped")
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(child)
                elif stat.S_ISREG(metadata.st_mode):
                    found.append(child)
        return tuple(found)

    def _recycle_bin_observations(self) -> tuple[StorageObservation, ...]:
        system_root = Path(os.environ.get("SYSTEMROOT", "C:/Windows"))
        volume = Path(f"{system_root.drive}\\")
        info = _SHQueryRBInfo()
        info.cbSize = ctypes.sizeof(info)
        query = cast(Any, self._shell32.SHQueryRecycleBinW)
        query.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(_SHQueryRBInfo)]
        query.restype = ctypes.c_long
        result = int(query(str(volume), ctypes.byref(info)))
        if result != _S_OK:
            return (
                StorageObservation(
                    category=CleanupCategory.RECYCLE_BIN_CONTENT,
                    source="windows-recycle-bin-summary",
                    availability=ObservationAvailability.UNAVAILABLE,
                    scope_decision=ScanScopeDecision.METADATA_ONLY,
                    ownership_confidence=OwnershipConfidence.UNKNOWN,
                    evidence=(OptimizationEvidence.SOURCE_UNAVAILABLE,),
                ),
            )
        return (
            StorageObservation(
                category=CleanupCategory.RECYCLE_BIN_CONTENT,
                source="windows-recycle-bin-summary",
                observed_size_bytes=max(0, int(info.i64Size)),
                item_count=max(0, int(info.i64NumItems)),
                availability=ObservationAvailability.AVAILABLE,
                scope_decision=ScanScopeDecision.METADATA_ONLY,
                ownership_confidence=OwnershipConfidence.HIGH,
                evidence=(OptimizationEvidence.WINDOWS_QUERY_API,),
            ),
        )

    @staticmethod
    def _protected_system_observations() -> tuple[StorageObservation, ...]:
        return tuple(
            StorageObservation(
                category=category,
                source=source,
                availability=ObservationAvailability.UNAVAILABLE,
                scope_decision=ScanScopeDecision.PROTECTED,
                ownership_confidence=OwnershipConfidence.UNKNOWN,
                evidence=(
                    OptimizationEvidence.SOURCE_UNAVAILABLE,
                    OptimizationEvidence.PROTECTION_POLICY,
                ),
                warnings=(reason,),
            )
            for category, source, reason in (
                (
                    CleanupCategory.WINDOWS_UPDATE_CANDIDATE,
                    "windows-update-managed-storage",
                    "Reliable read-only reclaim data is unavailable; WinSxS is not scanned",
                ),
                (
                    CleanupCategory.DELIVERY_OPTIMIZATION_CACHE,
                    "delivery-optimization-managed-storage",
                    "No reliable non-elevated size source is available",
                ),
                (
                    CleanupCategory.INSTALLER_CACHE_CANDIDATE,
                    "windows-installer-cache",
                    "Windows Installer cache is system protected",
                ),
            )
        )

    @staticmethod
    def _missing_observation(descriptor: _KnownRoot) -> StorageObservation:
        return StorageObservation(
            category=descriptor.category,
            source=descriptor.source,
            path=descriptor.path,
            availability=ObservationAvailability.SKIPPED,
            scope_decision=ScanScopeDecision.METADATA_ONLY,
            ownership_confidence=OwnershipConfidence.HIGH,
            evidence=(OptimizationEvidence.KNOWN_LOCATION_ALLOWLIST,),
            warnings=("Known location is not present",),
            source_reference=CleanupSourceReference(origin=CleanupEvidenceOrigin.KNOWN_LOCATION),
        )

    @staticmethod
    def _partial_observation(descriptor: _KnownRoot, exc: Exception) -> StorageObservation:
        return StorageObservation(
            category=descriptor.category,
            source=descriptor.source,
            path=descriptor.path,
            availability=ObservationAvailability.PARTIAL,
            scope_decision=ScanScopeDecision.METADATA_ONLY,
            ownership_confidence=OwnershipConfidence.HIGH,
            evidence=(
                OptimizationEvidence.KNOWN_LOCATION_ALLOWLIST,
                OptimizationEvidence.ACCESS_DENIED,
            ),
            warnings=(type(exc).__name__,),
            source_reference=CleanupSourceReference(origin=CleanupEvidenceOrigin.KNOWN_LOCATION),
        )

    @staticmethod
    def _warnings(value: object) -> tuple[str, ...]:
        if isinstance(value, tuple) and len(value) >= 2 and isinstance(value[1], tuple):
            return cast(tuple[str, ...], value[1])
        return ()

    @staticmethod
    def _item_count(collector: SystemCollector, value: object) -> int:
        if collector in {SystemCollector.SYSTEM_INFO, SystemCollector.CPU, SystemCollector.MEMORY}:
            return 1
        if isinstance(value, tuple) and value and isinstance(value[0], tuple):
            return len(value[0])
        if collector is SystemCollector.PROCESSES and isinstance(value, tuple):
            collection = value[0]
            return len(getattr(collection, "processes", ()))
        return 0

    @staticmethod
    def _duration_ms(started: float) -> int:
        return max(0, round((time.monotonic() - started) * 1_000))

    def _cancelled_outcome(self, collector: SystemCollector, started: float) -> CollectorOutcome:
        return CollectorOutcome(
            collector=collector,
            state=CollectorState.CANCELLED,
            duration_ms=self._duration_ms(started),
            error=CollectorError(code="cancelled", message="Stage 4E1 collection was cancelled"),
        )
