"""Strictly read-only models for Stage 4E1 system optimization analysis."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.system_diagnostics import SystemCollector, SystemSnapshot


class OptimizationGoal(StrEnum):
    """Finite user goals accepted by the local Stage 4E1 planner."""

    FREE_DISK_SPACE = "FREE_DISK_SPACE"
    IMPROVE_BOOT_TIME = "IMPROVE_BOOT_TIME"
    DIAGNOSE_SLOW_PC = "DIAGNOSE_SLOW_PC"
    REDUCE_BACKGROUND_LOAD = "REDUCE_BACKGROUND_LOAD"
    IMPROVE_RESPONSIVENESS = "IMPROVE_RESPONSIVENESS"
    GENERAL_HEALTH_CHECK = "GENERAL_HEALTH_CHECK"


class OptimizationToolName(StrEnum):
    """Complete Stage 4E1 tool allow-list; no mutation verb is representable."""

    SNAPSHOT = "optimization.snapshot"
    STORAGE_ANALYZE = "optimization.storage.analyze"
    CLEANUP_CANDIDATES_ANALYZE = "optimization.cleanup_candidates.analyze"
    PERFORMANCE_ANALYZE = "optimization.performance.analyze"
    RECOMMENDATIONS = "optimization.recommendations"


class CleanupCategory(StrEnum):
    """Finite origin classification for a storage observation."""

    USER_TEMP = "USER_TEMP"
    SYSTEM_TEMP = "SYSTEM_TEMP"
    APPLICATION_CACHE = "APPLICATION_CACHE"
    BROWSER_CACHE = "BROWSER_CACHE"
    LOG = "LOG"
    CRASH_DUMP = "CRASH_DUMP"
    RECYCLE_BIN_CONTENT = "RECYCLE_BIN_CONTENT"
    WINDOWS_UPDATE_CANDIDATE = "WINDOWS_UPDATE_CANDIDATE"
    DELIVERY_OPTIMIZATION_CACHE = "DELIVERY_OPTIMIZATION_CACHE"
    INSTALLER_CACHE_CANDIDATE = "INSTALLER_CACHE_CANDIDATE"
    PROGRAM_RESIDUAL = "PROGRAM_RESIDUAL"
    OBSOLETE_SHORTCUT = "OBSOLETE_SHORTCUT"
    LARGE_FILE = "LARGE_FILE"
    INACTIVE_LARGE_FILE = "INACTIVE_LARGE_FILE"
    DUPLICATE_FILE = "DUPLICATE_FILE"
    UNKNOWN = "UNKNOWN"


class CleanupSafetyClassification(StrEnum):
    """Safety meaning of a candidate, independent from ownership confidence."""

    LOW_RISK_CANDIDATE = "LOW_RISK_CANDIDATE"
    CAUTION = "CAUTION"
    PROTECTED = "PROTECTED"
    HIGH_IMPACT = "HIGH_IMPACT"
    UNKNOWN = "UNKNOWN"
    BLOCKED = "BLOCKED"


class ProtectionLevel(StrEnum):
    """Data protection strength attached to an observed location."""

    NONE = "NONE"
    CAUTION = "CAUTION"
    PROTECTED = "PROTECTED"
    STRONGLY_PROTECTED = "STRONGLY_PROTECTED"
    SYSTEM_PROTECTED = "SYSTEM_PROTECTED"
    UNKNOWN = "UNKNOWN"


class OptimizationConfidence(StrEnum):
    """Truthful certainty for evidence, findings, and estimates."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class OwnershipConfidence(StrEnum):
    """Certainty that an observation belongs to the described source."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class OptimizationEvidence(StrEnum):
    """Finite evidence sources; free-form model claims cannot become evidence."""

    WINDOWS_QUERY_API = "WINDOWS_QUERY_API"
    DIRECT_FILE_METADATA = "DIRECT_FILE_METADATA"
    KNOWN_LOCATION_ALLOWLIST = "KNOWN_LOCATION_ALLOWLIST"
    AUTHORIZED_STAGE1_SCOPE = "AUTHORIZED_STAGE1_SCOPE"
    STAGE1_VERIFIED_DUPLICATE_REPORT = "STAGE1_VERIFIED_DUPLICATE_REPORT"
    STAGE4D3_EXACT_RESIDUAL_REPORT = "STAGE4D3_EXACT_RESIDUAL_REPORT"
    MULTI_SAMPLE_COUNTER = "MULTI_SAMPLE_COUNTER"
    REGISTRY_ESTIMATED_SIZE = "REGISTRY_ESTIMATED_SIZE"
    PROTECTION_POLICY = "PROTECTION_POLICY"
    ACCESS_DENIED = "ACCESS_DENIED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    PARTIAL_ENUMERATION = "PARTIAL_ENUMERATION"


class CleanupReasonCode(StrEnum):
    """Machine-readable reasons used instead of unbounded LLM explanations."""

    KNOWN_TEMP_LOCATION = "KNOWN_TEMP_LOCATION"
    KNOWN_CACHE_LOCATION = "KNOWN_CACHE_LOCATION"
    KNOWN_LOG_LOCATION = "KNOWN_LOG_LOCATION"
    KNOWN_DUMP_LOCATION = "KNOWN_DUMP_LOCATION"
    RECYCLE_BIN_SUMMARY = "RECYCLE_BIN_SUMMARY"
    USER_AUTHORIZED_LARGE_FILE = "USER_AUTHORIZED_LARGE_FILE"
    POSSIBLY_INACTIVE = "POSSIBLY_INACTIVE"
    VERIFIED_DUPLICATE_GROUP = "VERIFIED_DUPLICATE_GROUP"
    EXACT_UNINSTALL_CONTEXT = "EXACT_UNINSTALL_CONTEXT"
    RECENT_ACTIVITY = "RECENT_ACTIVITY"
    USER_DATA_MAY_BE_PRESENT = "USER_DATA_MAY_BE_PRESENT"
    SYSTEM_MANAGED = "SYSTEM_MANAGED"
    PROTECTED_COMPONENT_STORE = "PROTECTED_COMPONENT_STORE"
    RELIABLE_SIZE_UNAVAILABLE = "RELIABLE_SIZE_UNAVAILABLE"
    ACCESS_INCOMPLETE = "ACCESS_INCOMPLETE"
    UNKNOWN_OWNERSHIP = "UNKNOWN_OWNERSHIP"


class ObservationAvailability(StrEnum):
    """Availability of one bounded read-only source."""

    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    SKIPPED = "SKIPPED"


class CleanupEvidenceOrigin(StrEnum):
    """Local provenance retained for safe hand-off; it grants no execution authority."""

    KNOWN_LOCATION = "KNOWN_LOCATION"
    STAGE1_REPORT = "STAGE1_REPORT"
    STAGE4D3_REPORT = "STAGE4D3_REPORT"


class CleanupSourceReference(BaseModel):
    """Opaque upstream references used to reopen an existing safe workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: CleanupEvidenceOrigin
    upstream_report_id: UUID | None = None
    upstream_candidate_id: UUID | None = None
    upstream_record_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_reference(self) -> CleanupSourceReference:
        """Require only the identifiers meaningful for the declared source."""
        if self.origin is CleanupEvidenceOrigin.STAGE4D3_REPORT:
            if self.upstream_report_id is None or self.upstream_candidate_id is None:
                raise ValueError("Stage 4D3 provenance requires report and candidate IDs")
            if self.upstream_record_id is not None:
                raise ValueError("Stage 4D3 provenance cannot contain a Stage 1 record ID")
        elif self.origin is CleanupEvidenceOrigin.STAGE1_REPORT:
            if self.upstream_record_id is None:
                raise ValueError("Stage 1 provenance requires a record ID")
            if self.upstream_candidate_id is not None:
                raise ValueError("Stage 1 provenance cannot contain a residual candidate ID")
        elif any(
            value is not None
            for value in (
                self.upstream_report_id,
                self.upstream_candidate_id,
                self.upstream_record_id,
            )
        ):
            raise ValueError("Known-location provenance cannot contain upstream IDs")
        return self


class ScanScopeDecision(StrEnum):
    """Read policy decision for a Stage 4E1 root."""

    SAFE_READ = "SAFE_READ"
    METADATA_ONLY = "METADATA_ONLY"
    AUTHORIZED_USER_PATH = "AUTHORIZED_USER_PATH"
    PROTECTED = "PROTECTED"
    UNSUPPORTED = "UNSUPPORTED"


class PerformanceCategory(StrEnum):
    """Finite diagnostic categories produced by deterministic rules."""

    CPU_PRESSURE = "CPU_PRESSURE"
    MEMORY_PRESSURE = "MEMORY_PRESSURE"
    DISK_SPACE_PRESSURE = "DISK_SPACE_PRESSURE"
    DISK_IO_PRESSURE = "DISK_IO_PRESSURE"
    STARTUP_LOAD = "STARTUP_LOAD"
    BACKGROUND_PROCESS_LOAD = "BACKGROUND_PROCESS_LOAD"
    LARGE_STORAGE_USAGE = "LARGE_STORAGE_USAGE"
    POSSIBLE_SOFTWARE_BLOAT = "POSSIBLE_SOFTWARE_BLOAT"
    NO_CLEAR_BOTTLENECK = "NO_CLEAR_BOTTLENECK"
    UNKNOWN = "UNKNOWN"


class ExpectedBenefit(StrEnum):
    """Conservative benefit estimate for a non-executable recommendation."""

    LOW = "LOW"
    MODERATE = "MODERATE"
    POTENTIALLY_HIGH = "POTENTIALLY_HIGH"
    UNKNOWN = "UNKNOWN"


class OptimizationPlan(BaseModel):
    """Immutable R0 plan whose exact digest is bound to user confirmation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: UUID = Field(default_factory=uuid4)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_goal: str = Field(min_length=1, max_length=2_000)
    summary: str = Field(min_length=1, max_length=1_000)
    goals: tuple[OptimizationGoal, ...] = Field(min_length=1, max_length=6)
    tools: tuple[OptimizationToolName, ...] = Field(min_length=3, max_length=5)
    snapshot_collectors: tuple[SystemCollector, ...] = Field(
        default_factory=lambda: tuple(SystemCollector), min_length=1, max_length=8
    )
    authorized_root_ids: tuple[UUID, ...] = Field(default=(), max_length=32)
    authorized_roots: tuple[Path, ...] = Field(default=(), max_length=32)
    max_objects: int = Field(default=25_000, ge=1, le=100_000)
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    minimum_large_file_bytes: int = Field(default=1024**3, ge=1024**2)
    inactive_days: int = Field(default=90, ge=1, le=3_650)
    sample_count: int = Field(default=3, ge=2, le=10)
    sample_interval_seconds: float = Field(default=0.5, ge=0.1, le=2.0)
    risk_level: RiskLevel = RiskLevel.R0
    read_only: bool = True
    requires_plan_confirmation: bool = True
    requires_runtime_confirmation: bool = False
    rollback_level: RollbackLevel = RollbackLevel.NONE
    estimated_system_changes: int = Field(default=0, ge=0, le=0)
    stage4e1_executable: bool = False

    @model_validator(mode="after")
    def validate_read_only_boundary(self) -> OptimizationPlan:
        """Fail closed if a plan weakens the Stage 4E1 zero-mutation contract."""
        canonical_tools = tuple(OptimizationToolName)
        expected_tools = tuple(item for item in canonical_tools if item in self.tools)
        if self.tools != expected_tools or len(set(self.tools)) != len(self.tools):
            raise ValueError("Stage 4E1 tools must be a unique canonical allow-list subset")
        if (
            not self.tools
            or self.tools[0] is not OptimizationToolName.SNAPSHOT
            or self.tools[-1] is not OptimizationToolName.RECOMMENDATIONS
        ):
            raise ValueError("Stage 4E1 plans require snapshot first and recommendations last")
        storage = OptimizationToolName.STORAGE_ANALYZE in self.tools
        cleanup = OptimizationToolName.CLEANUP_CANDIDATES_ANALYZE in self.tools
        if storage != cleanup:
            raise ValueError("Storage observation and cleanup classification are inseparable")
        canonical_collectors = tuple(SystemCollector)
        expected_collectors = tuple(
            item for item in canonical_collectors if item in self.snapshot_collectors
        )
        if self.snapshot_collectors != expected_collectors or len(
            set(self.snapshot_collectors)
        ) != len(self.snapshot_collectors):
            raise ValueError("Snapshot collectors must be a unique canonical allow-list subset")
        if storage and SystemCollector.DISKS not in self.snapshot_collectors:
            raise ValueError("Storage analysis requires the bounded disk collector")
        if self.risk_level is not RiskLevel.R0 or not self.read_only:
            raise ValueError("Stage 4E1 plans must be read-only R0")
        if not self.requires_plan_confirmation or self.requires_runtime_confirmation:
            raise ValueError("Stage 4E1 requires plan confirmation only")
        if self.rollback_level is not RollbackLevel.NONE or self.stage4e1_executable:
            raise ValueError("Stage 4E1 cannot authorize execution or rollback")
        if len(self.authorized_root_ids) != len(self.authorized_roots):
            raise ValueError("Authorized root IDs and resolved paths must match exactly")
        if len(set(self.authorized_root_ids)) != len(self.authorized_root_ids):
            raise ValueError("Authorized root IDs must be unique")
        return self

    def canonical_digest(self) -> str:
        """Return a stable digest for confirmation and stale-plan rejection."""
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class StorageObservation(BaseModel):
    """Metadata-only aggregate from one explicitly bounded source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: UUID = Field(default_factory=uuid4)
    category: CleanupCategory
    source: str = Field(min_length=1, max_length=200)
    path: Path | None = None
    observed_size_bytes: int = Field(default=0, ge=0)
    item_count: int = Field(default=0, ge=0)
    oldest_modified_at: datetime | None = None
    newest_modified_at: datetime | None = None
    availability: ObservationAvailability
    scope_decision: ScanScopeDecision
    ownership_confidence: OwnershipConfidence
    evidence: tuple[OptimizationEvidence, ...] = Field(min_length=1)
    source_safety_classification: CleanupSafetyClassification | None = None
    source_protection_level: ProtectionLevel | None = None
    source_confidence: OptimizationConfidence | None = None
    source_reason_codes: tuple[CleanupReasonCode, ...] = ()
    warnings: tuple[str, ...] = ()
    source_reference: CleanupSourceReference | None = None


class CleanupCandidate(BaseModel):
    """Non-authoritative report item that can never be executed in Stage 4E1."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: UUID = Field(default_factory=uuid4)
    category: CleanupCategory
    source: str = Field(min_length=1, max_length=200)
    path: Path | None = None
    observed_size_bytes: int = Field(ge=0)
    potential_reclaim_bytes: int | None = Field(default=None, ge=0)
    item_count: int = Field(ge=0)
    ownership_confidence: OwnershipConfidence
    safety_classification: CleanupSafetyClassification
    protection_level: ProtectionLevel
    recoverability: RollbackLevel
    confidence: OptimizationConfidence
    evidence: tuple[OptimizationEvidence, ...] = Field(min_length=1)
    reason_codes: tuple[CleanupReasonCode, ...] = Field(min_length=1)
    future_admin_requirement: bool | None = None
    source_reference: CleanupSourceReference | None = None
    stage4e1_executable: bool = False

    @model_validator(mode="after")
    def prohibit_actionable_protected_estimates(self) -> CleanupCandidate:
        """Protected/unknown observations cannot be presented as reclaimable."""
        if self.stage4e1_executable:
            raise ValueError("Stage 4E1 candidates are never executable")
        if (
            self.safety_classification
            in {
                CleanupSafetyClassification.PROTECTED,
                CleanupSafetyClassification.BLOCKED,
                CleanupSafetyClassification.UNKNOWN,
            }
            and self.potential_reclaim_bytes is not None
        ):
            raise ValueError("Protected, blocked, or unknown data cannot be reclaimable")
        return self


class PerformanceFinding(BaseModel):
    """One point-in-time finding supported by explicit deterministic evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: UUID = Field(default_factory=uuid4)
    category: PerformanceCategory
    title: str = Field(min_length=1, max_length=300)
    explanation: str = Field(min_length=1, max_length=2_000)
    confidence: OptimizationConfidence
    evidence_types: tuple[OptimizationEvidence, ...] = Field(min_length=1)
    evidence: dict[str, JsonValue]
    limitations: tuple[str, ...] = ()
    stage4e1_executable: bool = False

    @model_validator(mode="after")
    def prohibit_execution(self) -> PerformanceFinding:
        """Reject any finding represented as an execution object."""
        if self.stage4e1_executable:
            raise ValueError("Stage 4E1 findings are descriptive only")
        return self


class OptimizationRecommendation(BaseModel):
    """Evidence-linked advice that deliberately carries no execution authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    recommendation_id: UUID = Field(default_factory=uuid4)
    goal: OptimizationGoal
    title: str = Field(min_length=1, max_length=300)
    explanation: str = Field(min_length=1, max_length=2_000)
    evidence_references: tuple[UUID, ...]
    expected_benefit: ExpectedBenefit
    confidence: OptimizationConfidence
    future_risk_level: RiskLevel
    future_stage: str | None = Field(default=None, max_length=50)
    executable_in_current_stage: bool = False

    @model_validator(mode="after")
    def prohibit_execution(self) -> OptimizationRecommendation:
        """Ensure recommendation text cannot become an execution capability."""
        if self.executable_in_current_stage:
            raise ValueError("Stage 4E1 recommendations cannot execute")
        return self


class OptimizationSnapshot(BaseModel):
    """Current read-only system and storage state with truthful partial status."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: UUID = Field(default_factory=uuid4)
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    system: SystemSnapshot
    storage_observations: tuple[StorageObservation, ...] = ()
    partial_sources: tuple[str, ...] = ()
    skipped_sources: tuple[str, ...] = ()


class SystemOptimizationReport(BaseModel):
    """Final Stage 4E1 report; it is evidence, never a future confirmation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    snapshot: OptimizationSnapshot
    cleanup_candidates: tuple[CleanupCandidate, ...]
    performance_findings: tuple[PerformanceFinding, ...]
    recommendations: tuple[OptimizationRecommendation, ...]
    observed_bytes: int = Field(ge=0)
    potential_reclaim_bytes: int | None = Field(default=None, ge=0)
    protected_bytes: int = Field(ge=0)
    unknown_bytes: int = Field(ge=0)
    partial_sources: tuple[str, ...] = ()
    skipped_sources: tuple[str, ...] = ()
    changes_performed: bool = False
    stage4e1_executable: bool = False

    @model_validator(mode="after")
    def enforce_report_boundary(self) -> SystemOptimizationReport:
        """Reject reports that imply a modification or reusable authority."""
        if self.changes_performed or self.stage4e1_executable:
            raise ValueError("Stage 4E1 reports cannot perform or authorize changes")
        return self


class SnapshotRequest(BaseModel):
    """Bounded arguments for the read-only system snapshot tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    collectors: tuple[SystemCollector, ...] = Field(min_length=1, max_length=8)
    sample_count: int = Field(ge=2, le=10)
    sample_interval_seconds: float = Field(ge=0.1, le=2.0)
    max_processes: int = Field(ge=1, le=2_000)
    max_items: int = Field(ge=1, le=20_000)


class SnapshotResult(BaseModel):
    """System portion returned by ``optimization.snapshot``."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    system: SystemSnapshot


class StorageAnalysisRequest(BaseModel):
    """Locally resolved roots and limits for metadata-only storage observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    authorized_roots: tuple[Path, ...] = Field(default=(), max_length=32)
    max_objects: int = Field(ge=1, le=100_000)
    timeout_seconds: float = Field(gt=0, le=600)
    minimum_large_file_bytes: int = Field(ge=1024**2)
    inactive_days: int = Field(ge=1, le=3_650)


class StorageAnalysisResult(BaseModel):
    """Metadata observations returned by the storage analysis tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    observations: tuple[StorageObservation, ...]
    partial_sources: tuple[str, ...] = ()
    skipped_sources: tuple[str, ...] = ()
    truncated: bool = False


class CleanupAnalysisRequest(BaseModel):
    """Validated observations passed to deterministic candidate classification."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    observations: tuple[StorageObservation, ...] = Field(max_length=100_000)
    inactive_days: int = Field(ge=1, le=3_650)


class CleanupAnalysisResult(BaseModel):
    """Non-executable candidate classification result."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    candidates: tuple[CleanupCandidate, ...]


class PerformanceAnalysisRequest(BaseModel):
    """Validated system snapshot for deterministic performance rules."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    system: SystemSnapshot


class PerformanceAnalysisResult(BaseModel):
    """Performance findings produced without calling a model."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    findings: tuple[PerformanceFinding, ...]


class RecommendationRequest(BaseModel):
    """Structured evidence supplied to the non-executing recommendation engine."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    goals: tuple[OptimizationGoal, ...] = Field(min_length=1, max_length=6)
    candidates: tuple[CleanupCandidate, ...] = Field(max_length=100_000)
    findings: tuple[PerformanceFinding, ...] = Field(max_length=10_000)


class RecommendationResult(BaseModel):
    """Recommendations returned by the final R0 tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    recommendations: tuple[OptimizationRecommendation, ...]
