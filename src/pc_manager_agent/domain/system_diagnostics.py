"""Provider-neutral models for read-only Windows system diagnostics."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel


class DiagnosticIntent(StrEnum):
    """Finite user intents understood by the Stage 3 planner."""

    OVERVIEW = "overview"
    PERFORMANCE = "performance"
    CPU = "cpu"
    MEMORY = "memory"
    DISKS = "disks"
    PROCESSES = "processes"
    STARTUP = "startup"
    SERVICES = "services"
    SOFTWARE = "software"


class SystemCollector(StrEnum):
    """Allow-listed collector identifiers; each maps to one registered R0 tool."""

    SYSTEM_INFO = "system.info"
    CPU = "system.cpu"
    MEMORY = "system.memory"
    DISKS = "system.disks"
    PROCESSES = "system.processes"
    STARTUP = "system.startup"
    SERVICES = "system.services"
    SOFTWARE = "system.software"


class CollectorState(StrEnum):
    """Outcome state for an individual collector."""

    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Confidence(StrEnum):
    """Conservative certainty attached to a deterministic finding."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FindingSeverity(StrEnum):
    """Non-alarmist diagnostic severity."""

    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"


class DiagnosticCategory(StrEnum):
    """Report categories used by the dashboard and rules engine."""

    CPU = "cpu"
    MEMORY = "memory"
    DISK = "disk"
    PROCESS = "process"
    STARTUP = "startup"
    SERVICE = "service"
    SOFTWARE = "software"
    SYSTEM = "system"


class SuggestedActionType(StrEnum):
    """Read-only or user-directed follow-up categories."""

    OBSERVE = "observe"
    REVIEW = "review"
    OPEN_WINDOWS_SETTINGS = "open_windows_settings"
    RUN_FOLLOW_UP_DIAGNOSTIC = "run_follow_up_diagnostic"


class DiskKind(StrEnum):
    """Windows drive classification used to separate local and remote storage."""

    FIXED = "fixed"
    REMOVABLE = "removable"
    NETWORK = "network"
    OPTICAL = "optical"
    RAMDISK = "ramdisk"
    UNKNOWN = "unknown"


class StartupSource(StrEnum):
    """Read-only locations from which startup entries can be observed."""

    HKCU_RUN = "hkcu_run"
    HKCU_RUN_ONCE = "hkcu_run_once"
    HKLM_RUN = "hklm_run"
    HKLM_RUN_ONCE = "hklm_run_once"
    USER_STARTUP_FOLDER = "user_startup_folder"
    COMMON_STARTUP_FOLDER = "common_startup_folder"


class SoftwareScope(StrEnum):
    """Registry scope containing an installed-software entry."""

    CURRENT_USER = "current_user"
    LOCAL_MACHINE = "local_machine"


class SoftwareArchitecture(StrEnum):
    """Registry view in which an installed-software entry was found."""

    NATIVE = "native"
    X86 = "x86"
    X64 = "x64"


class AccessCompleteness(StrEnum):
    """Whether protected process fields were fully available."""

    COMPLETE = "complete"
    PARTIAL = "partial"


class DiagnosticThresholds(BaseModel):
    """Centralized, reportable thresholds for conservative Stage 3 rules."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cpu_notice_percent: float = Field(default=75.0, ge=0, le=100)
    cpu_warning_percent: float = Field(default=90.0, ge=0, le=100)
    memory_notice_percent: float = Field(default=80.0, ge=0, le=100)
    memory_warning_percent: float = Field(default=92.0, ge=0, le=100)
    disk_notice_percent: float = Field(default=85.0, ge=0, le=100)
    disk_warning_percent: float = Field(default=95.0, ge=0, le=100)
    disk_notice_free_bytes: int = Field(default=20 * 1024**3, ge=0)
    disk_warning_free_bytes: int = Field(default=5 * 1024**3, ge=0)
    startup_notice_count: int = Field(default=20, ge=1, le=10_000)
    process_cpu_notice_percent: float = Field(default=25.0, ge=0, le=100)
    process_memory_notice_percent: float = Field(default=10.0, ge=0, le=100)


class DiagnosticPlan(BaseModel):
    """Immutable local plan for a finite set of registered read-only collectors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: UUID = Field(default_factory=uuid4)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: str = Field(min_length=1, max_length=500)
    user_goal: str = Field(min_length=1, max_length=2_000)
    intent: DiagnosticIntent
    collectors: tuple[SystemCollector, ...] = Field(min_length=1, max_length=8)
    sample_count: int = Field(default=3, ge=2, le=10)
    sample_interval_seconds: float = Field(default=1.5, ge=0.1, le=2.0)
    max_processes: int = Field(default=500, ge=1, le=2_000)
    max_items_per_collector: int = Field(default=5_000, ge=1, le=20_000)
    risk_level: RiskLevel = RiskLevel.R0
    read_only: bool = True
    requires_plan_confirmation: bool = True
    rollback_level: RollbackLevel = RollbackLevel.NONE
    estimated_system_changes: int = Field(default=0, ge=0, le=0)

    @model_validator(mode="after")
    def validate_safety_contract(self) -> DiagnosticPlan:
        """Reject any plan that weakens the immutable R0 execution contract."""
        if self.risk_level is not RiskLevel.R0 or not self.read_only:
            raise ValueError("Stage 3 diagnostic plans must be read-only R0")
        if not self.requires_plan_confirmation:
            raise ValueError("Stage 3 diagnostic plans require plan confirmation")
        if len(set(self.collectors)) != len(self.collectors):
            raise ValueError("Diagnostic collectors must be unique")
        return self

    def canonical_digest(self) -> str:
        """Return a stable digest that binds a confirmation to this exact plan."""
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class DiagnosticIntentDraft(BaseModel):
    """Untrusted finite intent returned by an optional model provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: DiagnosticIntent
    requested_collectors: tuple[SystemCollector, ...] = Field(min_length=1, max_length=8)


class SystemInfoSnapshot(BaseModel):
    """Read-only operating-system and hardware identity summary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    computer_name: str = Field(min_length=1, max_length=255)
    windows_edition: str | None = Field(default=None, max_length=255)
    windows_release: str = Field(min_length=1, max_length=100)
    windows_build: str = Field(min_length=1, max_length=100)
    architecture: str = Field(min_length=1, max_length=100)
    processor_model: str | None = Field(default=None, max_length=500)
    installed_ram_bytes: int = Field(ge=0)
    boot_time: datetime
    uptime_seconds: int = Field(ge=0)


class CpuSample(BaseModel):
    """One system-wide and per-core CPU utilization observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    total_percent: float = Field(ge=0, le=100)
    per_core_percent: tuple[float, ...]


class CpuSnapshot(BaseModel):
    """Multi-sample CPU snapshot; no single instantaneous value drives findings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    samples: tuple[CpuSample, ...] = Field(min_length=2, max_length=10)
    average_percent: float = Field(ge=0, le=100)
    peak_percent: float = Field(ge=0, le=100)
    physical_cores: int | None = Field(default=None, ge=1)
    logical_cores: int = Field(ge=1)
    current_frequency_mhz: float | None = Field(default=None, ge=0)


class MemorySnapshot(BaseModel):
    """Physical and virtual memory observations in bytes and percentages."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    total_bytes: int = Field(ge=0)
    available_bytes: int = Field(ge=0)
    used_bytes: int = Field(ge=0)
    used_percent: float = Field(ge=0, le=100)
    cached_bytes: int | None = Field(default=None, ge=0)
    pagefile_total_bytes: int | None = Field(default=None, ge=0)
    pagefile_used_bytes: int | None = Field(default=None, ge=0)
    pagefile_used_percent: float | None = Field(default=None, ge=0, le=100)
    commit_limit_bytes: int | None = Field(default=None, ge=0)
    commit_used_bytes: int | None = Field(default=None, ge=0)


class DiskSnapshot(BaseModel):
    """Read-only capacity observation for one mounted Windows volume."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    device: str = Field(min_length=1, max_length=500)
    mountpoint: Path
    filesystem: str | None = Field(default=None, max_length=100)
    kind: DiskKind
    total_bytes: int = Field(ge=0)
    used_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)
    used_percent: float = Field(ge=0, le=100)


class ProcessSnapshot(BaseModel):
    """Conservative per-process metadata that deliberately excludes command lines."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pid: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=500)
    executable_path: Path | None = None
    username: str | None = Field(default=None, max_length=500)
    status: str | None = Field(default=None, max_length=100)
    started_at: datetime | None = None
    cpu_percent: float = Field(ge=0)
    memory_rss_bytes: int = Field(ge=0)
    memory_percent: float = Field(ge=0, le=100)
    thread_count: int | None = Field(default=None, ge=0)
    parent_pid: int | None = Field(default=None, ge=0)
    access: AccessCompleteness = AccessCompleteness.COMPLETE


class ProcessGroupSnapshot(BaseModel):
    """Deterministic grouping by normalized process name, not guessed application identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    normalized_name: str = Field(min_length=1, max_length=500)
    process_count: int = Field(ge=1)
    total_cpu_percent: float = Field(ge=0)
    total_memory_rss_bytes: int = Field(ge=0)
    pids: tuple[int, ...]


class ProcessCollection(BaseModel):
    """Processes, conservative name groups, and protected-process accounting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    processes: tuple[ProcessSnapshot, ...]
    groups: tuple[ProcessGroupSnapshot, ...]
    complete_count: int = Field(ge=0)
    partial_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    truncated: bool = False


class StartupEntry(BaseModel):
    """One observed registry or Startup-folder entry; never an execution request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=500)
    source: StartupSource
    scope: SoftwareScope
    architecture: SoftwareArchitecture = SoftwareArchitecture.NATIVE
    command_or_path: str = Field(min_length=1, max_length=4_096)
    enabled: bool | None = None


class ServiceSnapshot(BaseModel):
    """Read-only Windows service configuration and current state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=500)
    display_name: str = Field(min_length=1, max_length=500)
    state: str = Field(min_length=1, max_length=100)
    start_type: str | None = Field(default=None, max_length=100)
    account: str | None = Field(default=None, max_length=500)
    executable_path: Path | None = None
    description: str | None = Field(default=None, max_length=4_000)


class InstalledSoftware(BaseModel):
    """One deduplicated uninstall-registry record; uninstall commands are excluded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=500)
    version: str | None = Field(default=None, max_length=500)
    publisher: str | None = Field(default=None, max_length=500)
    install_date: str | None = Field(default=None, max_length=100)
    install_location: Path | None = None
    estimated_size_bytes: int | None = Field(default=None, ge=0)
    uninstall_entry_present: bool = True
    scope: SoftwareScope
    architecture: SoftwareArchitecture
    registry_key: str = Field(min_length=1, max_length=2_000)


class CollectorError(BaseModel):
    """Sanitized collector error that supports partial diagnostic reports."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1_000)
    recoverable: bool = True


class CollectorOutcome(BaseModel):
    """Status and counts for one collector without storing sensitive raw payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    collector: SystemCollector
    state: CollectorState
    item_count: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    from_cache: bool = False
    warnings: tuple[str, ...] = ()
    error: CollectorError | None = None


class SystemSnapshot(BaseModel):
    """Aggregate of successful Stage 3 collector outputs with partial-failure metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    system_info: SystemInfoSnapshot | None = None
    cpu: CpuSnapshot | None = None
    memory: MemorySnapshot | None = None
    disks: tuple[DiskSnapshot, ...] = ()
    processes: ProcessCollection | None = None
    startup_entries: tuple[StartupEntry, ...] = ()
    services: tuple[ServiceSnapshot, ...] = ()
    software: tuple[InstalledSoftware, ...] = ()
    outcomes: tuple[CollectorOutcome, ...]


class SuggestedAction(BaseModel):
    """Non-executing follow-up shown to the user after deterministic analysis."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_type: SuggestedActionType
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=1_000)
    executable: bool = False
    risk_level: RiskLevel = RiskLevel.R0

    @model_validator(mode="after")
    def prohibit_execution(self) -> SuggestedAction:
        """Keep Stage 3 suggestions descriptive and non-mutating."""
        if self.executable:
            raise ValueError("Stage 3 suggested actions cannot execute system changes")
        return self


class DiagnosticFinding(BaseModel):
    """One deterministic, evidence-linked and non-diagnostic observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=100)
    category: DiagnosticCategory
    severity: FindingSeverity
    confidence: Confidence
    title: str = Field(min_length=1, max_length=300)
    explanation: str = Field(min_length=1, max_length=2_000)
    evidence: dict[str, JsonValue]
    threshold: dict[str, JsonValue]
    actions: tuple[SuggestedAction, ...]


class DiagnosticReport(BaseModel):
    """Complete read-only report, including actual thresholds and partial failures."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: str = Field(min_length=1, max_length=2_000)
    snapshot: SystemSnapshot
    findings: tuple[DiagnosticFinding, ...]
    thresholds: DiagnosticThresholds
    disclaimer: str = (
        "This is a point-in-time, read-only observation, not a malware, medical, "
        "hardware-failure, or root-cause diagnosis."
    )


class EmptyCollectorRequest(BaseModel):
    """Validated input for a collector that needs no caller-controlled arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class LimitedCollectorRequest(BaseModel):
    """Validated item limit for finite registry and SCM enumeration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_items: int = Field(default=5_000, ge=1, le=20_000)


class CpuCollectorRequest(BaseModel):
    """Bounded CPU sampling parameters."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_count: int = Field(default=3, ge=2, le=10)
    interval_seconds: float = Field(default=0.5, ge=0.1, le=2.0)


class ProcessCollectorRequest(BaseModel):
    """Bounded process sampling parameters; command-line collection is impossible."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_interval_seconds: float = Field(default=0.5, ge=0.1, le=2.0)
    max_processes: int = Field(default=500, ge=1, le=2_000)


class SystemInfoResult(BaseModel):
    """Declared output schema for the system information tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    snapshot: SystemInfoSnapshot
    warnings: tuple[str, ...] = ()


class CpuResult(BaseModel):
    """Declared output schema for the CPU tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    snapshot: CpuSnapshot
    warnings: tuple[str, ...] = ()


class MemoryResult(BaseModel):
    """Declared output schema for the memory tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    snapshot: MemorySnapshot
    warnings: tuple[str, ...] = ()


class DiskResult(BaseModel):
    """Declared output schema for the local disk tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    snapshots: tuple[DiskSnapshot, ...]
    warnings: tuple[str, ...] = ()


class ProcessResult(BaseModel):
    """Declared output schema for the process tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    collection: ProcessCollection
    warnings: tuple[str, ...] = ()


class StartupResult(BaseModel):
    """Declared output schema for the startup inventory tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    entries: tuple[StartupEntry, ...]
    warnings: tuple[str, ...] = ()
    truncated: bool = False


class ServiceResult(BaseModel):
    """Declared output schema for the service inventory tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    services: tuple[ServiceSnapshot, ...]
    warnings: tuple[str, ...] = ()
    truncated: bool = False


class SoftwareResult(BaseModel):
    """Declared output schema for the installed-software inventory tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    software: tuple[InstalledSoftware, ...]
    warnings: tuple[str, ...] = ()
    truncated: bool = False
