"""Structured intent, plan, progress, candidate, and summary models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.plans import FrozenModel, TaskPlan
from pc_manager_agent.domain.reports import FileCategory, FileMetadata, ScanIssue, ScanStatus
from pc_manager_agent.domain.risk import RiskLevel


class AnalysisType(StrEnum):
    """Deterministic analysis capabilities available in Stage 1."""

    LARGE_FILES = "large_files"
    INACTIVE_FILES = "inactive_files"
    DUPLICATES = "duplicates"


class AnalysisMatchMode(StrEnum):
    """Whether a result must satisfy every selected analysis or any one."""

    ALL = "all"
    ANY = "any"


class InactiveConfidence(StrEnum):
    """Confidence in a cautious inactive-file assessment."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class AnalysisErrorSeverity(StrEnum):
    """Whether an analysis problem is fatal, recoverable, or skipped."""

    FATAL = "fatal"
    RECOVERABLE = "recoverable"
    SKIPPED = "skipped"


class FileAnalysisFilters(FrozenModel):
    """Validated thresholds selected by the user or an untrusted planner draft."""

    minimum_size_bytes: int = Field(default=1_073_741_824, ge=1, le=10**16)
    inactive_days: int = Field(default=90, ge=1, le=3_650)


class FileAnalysisIntentDraft(FrozenModel):
    """Provider result that references only local authorized-root identifiers."""

    intent: Literal["analyze_files"] = "analyze_files"
    authorized_root_ids: tuple[UUID, ...]
    filters: FileAnalysisFilters = Field(default_factory=FileAnalysisFilters)
    analyses: tuple[AnalysisType, ...]
    match_mode: AnalysisMatchMode = AnalysisMatchMode.ALL
    risk_level: Literal[RiskLevel.R0] = RiskLevel.R0
    read_only: Literal[True] = True

    @model_validator(mode="after")
    def require_scope_and_analysis(self) -> Self:
        """Reject empty or duplicated root and analysis selections."""
        if not self.authorized_root_ids:
            raise ValueError("At least one authorized root identifier is required")
        if len(self.authorized_root_ids) != len(set(self.authorized_root_ids)):
            raise ValueError("Authorized root identifiers must be unique")
        if not self.analyses:
            raise ValueError("At least one analysis type is required")
        if len(self.analyses) != len(set(self.analyses)):
            raise ValueError("Analysis types must be unique")
        return self


class FileAnalysisPlan(FrozenModel):
    """Semantic analysis plan paired with its executable registered-tool plan."""

    analysis_session_id: UUID = Field(default_factory=uuid4)
    authorized_root_ids: tuple[UUID, ...]
    filters: FileAnalysisFilters
    analyses: tuple[AnalysisType, ...]
    match_mode: AnalysisMatchMode = AnalysisMatchMode.ALL
    task_plan: TaskPlan
    risk_level: Literal[RiskLevel.R0] = RiskLevel.R0
    read_only: Literal[True] = True

    @model_validator(mode="after")
    def validate_task_plan(self) -> Self:
        """Ensure the executable plan cannot contradict read-only semantics."""
        if self.task_plan.estimated_impact.files_modified != 0:
            raise ValueError("A file analysis plan cannot modify files")
        if self.task_plan.estimated_impact.files_deleted != 0:
            raise ValueError("A file analysis plan cannot delete files")
        if any(step.risk_level is not RiskLevel.R0 for step in self.task_plan.steps):
            raise ValueError("Every file analysis step must be R0")
        return self

    def canonical_digest(self) -> str:
        """Return the confirmation digest of the fully populated task plan."""
        return self.task_plan.canonical_digest()


class InactiveAssessment(FrozenModel):
    """Evidence-based, non-prescriptive inactive-file assessment."""

    possibly_inactive: bool
    confidence: InactiveConfidence
    evidence: tuple[str, ...]
    threshold_days: int = Field(ge=1)


class DuplicateGroup(FrozenModel):
    """Files proven equal through SHA-256 and optional byte verification."""

    group_id: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)
    files: tuple[FileMetadata, ...]
    byte_verified: bool


class AnalysisCandidate(FrozenModel):
    """One paged GUI row composed from real scanner and analyzer output."""

    metadata: FileMetadata
    is_large: bool = False
    inactive: InactiveAssessment | None = None
    duplicate_group_id: str | None = None


class StoredFileRecord(FrozenModel):
    """One metadata row and its deterministic analysis annotations."""

    record_id: int = Field(ge=1)
    metadata: FileMetadata
    is_large: bool = False
    inactive: InactiveAssessment | None = None
    duplicate_group_id: str | None = None
    matches_plan: bool = False


class CategorySummary(FrozenModel):
    """Aggregate count and bytes for one file category."""

    category: FileCategory
    files: int = Field(ge=0)
    total_bytes: int = Field(ge=0)


class FileAnalysisSummary(FrozenModel):
    """Immutable aggregate values used by the GUI and optional LLM explanation."""

    files_scanned: int = Field(ge=0)
    directories_scanned: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    matching_files: int = Field(ge=0)
    matching_bytes: int = Field(ge=0)
    errors: int = Field(ge=0)
    status: ScanStatus
    categories: tuple[CategorySummary, ...] = ()


class FileAnalysisReport(FrozenModel):
    """Terminal analysis result; candidate rows remain in the paged local store."""

    analysis_session_id: UUID
    plan_id: UUID
    started_at: datetime
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: FileAnalysisSummary
    duplicate_groups: tuple[DuplicateGroup, ...] = ()
    issues: tuple[ScanIssue, ...] = ()


class FileAnalysisProgress(FrozenModel):
    """Progress event transported from worker threads to the GUI."""

    analysis_session_id: UUID
    phase: str
    files_scanned: int = Field(ge=0)
    directories_scanned: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    matching_files: int = Field(default=0, ge=0)
    errors: int = Field(default=0, ge=0)
    completed_units: int | None = Field(default=None, ge=0)
    total_units: int | None = Field(default=None, ge=0)


class LargeFileAnalysisRequest(FrozenModel):
    """Input for the registered large-file analyzer."""

    analysis_session_id: UUID
    minimum_size_bytes: int = Field(ge=1, le=10**16)


class LargeFileAnalysisResult(FrozenModel):
    """Aggregate outcome of a large-file threshold analysis."""

    analysis_session_id: UUID
    files: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    by_extension: dict[str, int]
    by_directory: dict[str, int]
    by_size_band: dict[str, int]
    cancelled: bool = False


class InactiveFileAnalysisRequest(FrozenModel):
    """Input for the registered cautious inactive-file analyzer."""

    analysis_session_id: UUID
    inactive_days: int = Field(ge=1, le=3_650)


class InactiveFileAnalysisResult(FrozenModel):
    """Aggregate outcome of an evidence-based inactive assessment."""

    analysis_session_id: UUID
    files: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    by_confidence: dict[InactiveConfidence, int]
    cancelled: bool = False


class DuplicateFileAnalysisRequest(FrozenModel):
    """Input for staged, read-only duplicate-content verification."""

    analysis_session_id: UUID
    quick_hash_bytes: int = Field(default=65_536, ge=4_096, le=4_194_304)
    byte_verify: bool = True


class DuplicateFileAnalysisResult(FrozenModel):
    """Verified duplicate groups plus recoverable hashing issues."""

    analysis_session_id: UUID
    groups: tuple[DuplicateGroup, ...]
    files: int = Field(ge=0)
    reclaimable_bytes: int = Field(ge=0)
    issues: tuple[ScanIssue, ...] = ()
    cancelled: bool = False
