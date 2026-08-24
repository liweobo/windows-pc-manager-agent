"""Provider-neutral contracts for Stage 4D3 read-only residual analysis."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.software_uninstall_analysis import canonical_digest


class UninstallMechanism(StrEnum):
    """Agent-controlled uninstall mechanisms eligible for Stage 4D3."""

    MSI = "msi"
    VENDOR = "vendor"
    WINGET = "winget"
    MSIX = "msix"


class ResidualSource(StrEnum):
    """Finite sources from which an exact residual path may originate."""

    INSTALL_LOCATION = "install_location"
    KNOWN_APP_DATA = "known_app_data"
    SHORTCUT = "shortcut"
    MSIX_INSTALL_LOCATION = "msix_install_location"
    MSIX_PACKAGE_DATA = "msix_package_data"
    SERVICE_ARTIFACT = "service_artifact"
    CONFIGURATION = "configuration"


class ResidualClassification(StrEnum):
    """Deterministic candidate categories that never imply deletion safety."""

    PROGRAM_RESIDUAL = "program_residual"
    CACHE = "cache"
    LOG = "log"
    TEMPORARY_DATA = "temporary_data"
    CONFIGURATION = "configuration"
    USER_DATA = "user_data"
    DATABASE = "database"
    PLUGIN_OR_EXTENSION = "plugin_or_extension"
    SHORTCUT = "shortcut"
    PACKAGE_USER_DATA = "package_user_data"
    SERVICE_RELATED_ARTIFACT = "service_related_artifact"
    APPLICATION_STATE = "application_state"
    CRASH_DUMP = "crash_dump"
    LICENSE_DATA = "license_data"
    UNKNOWN = "unknown"


class OwnershipConfidence(StrEnum):
    """Confidence that a candidate belongs to the uninstalled software."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class OwnershipEvidenceStrength(StrEnum):
    """Strength of one structured ownership observation."""

    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    NONE = "none"


class UserDataProtectionLevel(StrEnum):
    """Protection applied independently from ownership confidence."""

    NONE = "none"
    CAUTION = "caution"
    PROTECTED = "protected"
    STRONGLY_PROTECTED = "strongly_protected"
    UNKNOWN = "unknown"


class ResidualObjectType(StrEnum):
    """Filesystem object types observed without following redirects."""

    FILE = "file"
    DIRECTORY = "directory"
    SHORTCUT = "shortcut"
    REPARSE_POINT = "reparse_point"
    OTHER = "other"


class ResidualRecommendation(StrEnum):
    """The only recommendations Stage 4D3 may produce."""

    REPORT = "report"
    PROTECT = "protect"
    REVIEW_MANUALLY = "review_manually"


class ResidualAnalysisStatus(StrEnum):
    """Terminal status for one bounded residual analysis."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    TRUNCATED = "truncated"
    FAILED = "failed"


class ContextPathEvidence(FrozenModel):
    """One exact path known before uninstall, never inferred from display text."""

    path: Path
    source: ResidualSource
    evidence_code: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    expected_classification: ResidualClassification
    max_depth: int = Field(default=6, ge=0, le=8)
    shared_location: bool = False
    related_target_path: Path | None = None

    @model_validator(mode="after")
    def validate_related_target(self) -> Self:
        """Allow target evidence only for an exact shortcut captured before uninstall."""
        if self.related_target_path is not None and self.source is not ResidualSource.SHORTCUT:
            raise ValueError("Related target evidence is available only for shortcuts")
        return self


class UninstallContext(FrozenModel):
    """Privacy-minimized durable evidence for one Agent-controlled uninstall."""

    context_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID
    mechanism: UninstallMechanism
    software_identity_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    display_name: str = Field(min_length=1, max_length=1_000)
    display_version: str | None = Field(default=None, max_length=500)
    publisher: str | None = Field(default=None, max_length=1_000)
    scope: str = Field(min_length=1, max_length=100)
    architecture: str = Field(min_length=1, max_length=100)
    original_install_location: Path | None = None
    msi_product_code: str | None = Field(default=None, max_length=100)
    package_id: str | None = Field(default=None, max_length=256)
    package_source: str | None = Field(default=None, max_length=200)
    msix_family_name: str | None = Field(default=None, max_length=255)
    msix_full_name: str | None = Field(default=None, max_length=500)
    known_paths: tuple[ContextPathEvidence, ...] = Field(default=(), max_length=32)
    uninstall_started_at: datetime
    uninstall_completed_at: datetime | None = None
    verification_state: str = Field(default="pending", min_length=1, max_length=100)
    verified_removed: bool = False
    context_complete: bool = False
    warnings: tuple[str, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def validate_path_evidence(self) -> Self:
        """Reject duplicate path/source evidence and contradictory completion claims."""
        keys = [(str(item.path).casefold(), item.source.value) for item in self.known_paths]
        if len(keys) != len(set(keys)):
            raise ValueError("Uninstall context contains duplicate path evidence")
        if self.context_complete and self.uninstall_completed_at is None:
            raise ValueError("A completed uninstall context requires a completion time")
        if self.verified_removed and not self.context_complete:
            raise ValueError("Verified removal requires a completed uninstall context")
        return self

    def canonical_digest(self) -> str:
        """Hash all identity, path, and verification evidence used by analysis."""
        return canonical_digest(self.model_dump(mode="json"))

    @property
    def eligible_for_analysis(self) -> bool:
        """Return whether the transaction reached a supported terminal state."""
        return self.context_complete and (
            self.verified_removed or self.verification_state == "completed_unverified"
        )


class OwnershipEvidence(FrozenModel):
    """One non-content ownership observation shown to the user as a reason code."""

    code: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    strength: OwnershipEvidenceStrength
    explanation: str = Field(min_length=1, max_length=500)


class ResidualIssue(FrozenModel):
    """One skipped object or bounded, non-fatal filesystem problem."""

    code: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    message: str = Field(min_length=1, max_length=1_000)
    path: Path | None = None


class ResidualIdentity(FrozenModel):
    """Stable metadata identity captured with ``lstat`` for future revalidation."""

    normalized_path: Path
    device_id: int = Field(ge=0)
    file_id: int = Field(ge=0)
    object_type: ResidualObjectType
    size_bytes: int = Field(ge=0)
    modified_time_ns: int = Field(ge=0)

    def canonical_digest(self) -> str:
        """Hash the complete metadata identity without reading file contents."""
        return canonical_digest(self.model_dump(mode="json"))


class ResidualCandidate(FrozenModel):
    """Metadata-only possible residual; it carries no execution capability."""

    candidate_id: UUID = Field(default_factory=uuid4)
    report_id: UUID
    identity: ResidualIdentity
    path: Path
    scan_root: Path
    source: ResidualSource
    object_type: ResidualObjectType
    size_bytes: int = Field(default=0, ge=0)
    created_at: datetime | None = None
    modified_at: datetime | None = None
    accessed_at: datetime | None = None
    classification: ResidualClassification
    classification_reasons: tuple[str, ...] = Field(default=(), max_length=20)
    ownership_confidence: OwnershipConfidence
    ownership_evidence: tuple[OwnershipEvidence, ...] = Field(default=(), max_length=20)
    protection_level: UserDataProtectionLevel
    protection_reasons: tuple[str, ...] = Field(default=(), max_length=20)
    risk_flags: tuple[str, ...] = Field(default=(), max_length=20)
    readable: bool
    reparse_or_symlink: bool
    recommendation: ResidualRecommendation


class ResidualReportSummary(FrozenModel):
    """Aggregate counts that exclude file contents and sensitive payloads."""

    candidates: int = Field(ge=0)
    files: int = Field(ge=0)
    directories: int = Field(ge=0)
    total_size_bytes: int = Field(ge=0)
    protected_size_bytes: int = Field(default=0, ge=0)
    unknown_size_bytes: int = Field(default=0, ge=0)
    issues: int = Field(ge=0)
    skipped_paths: int = Field(default=0, ge=0)
    reparse_points_skipped: int = Field(default=0, ge=0)
    roots_requested: int = Field(ge=0)
    roots_scanned: int = Field(ge=0)
    duration_ms: int = Field(ge=0)


class ResidualReport(FrozenModel):
    """Complete Stage 4D3 report with a hard zero-deletion assertion."""

    report_id: UUID = Field(default_factory=uuid4)
    context_id: UUID
    uninstall_transaction_id: UUID
    plan_id: UUID
    software_identity_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_policy_version: str = Field(default="stage4d3-v1", pattern=r"^[a-z0-9.-]+$")
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime
    status: ResidualAnalysisStatus
    candidates: tuple[ResidualCandidate, ...] = Field(default=(), max_length=25_000)
    issues: tuple[ResidualIssue, ...] = Field(default=(), max_length=5_000)
    warnings: tuple[str, ...] = Field(default=(), max_length=200)
    summary: ResidualReportSummary
    deletion_performed: bool = False

    @model_validator(mode="after")
    def enforce_report_only(self) -> Self:
        """Make a destructive or internally inconsistent report unrepresentable."""
        if self.deletion_performed:
            raise ValueError("Stage 4D3 cannot report that deletion was performed")
        if any(candidate.report_id != self.report_id for candidate in self.candidates):
            raise ValueError("Residual candidate belongs to another report")
        if self.summary.candidates != len(self.candidates):
            raise ValueError("Residual summary candidate count does not match")
        if self.summary.issues != len(self.issues):
            raise ValueError("Residual summary issue count does not match")
        return self

    def to_model_payload(self) -> ResidualModelPayload:
        """Return a redacted, metadata-only explanation payload for an LLM."""
        return ResidualModelPayload(
            report_id=self.report_id,
            status=self.status,
            summary=self.summary,
            candidates=tuple(
                ResidualModelCandidate(
                    candidate_id=candidate.candidate_id,
                    display_path=_redacted_model_path(candidate.path),
                    object_type=candidate.object_type,
                    size_bytes=candidate.size_bytes,
                    classification=candidate.classification,
                    ownership_confidence=candidate.ownership_confidence,
                    evidence_codes=tuple(item.code for item in candidate.ownership_evidence),
                    protection_level=candidate.protection_level,
                    risk_flags=candidate.risk_flags,
                )
                for candidate in self.candidates
            ),
            deletion_performed=False,
        )


class ResidualModelCandidate(FrozenModel):
    """Redacted candidate fields allowed in provider explanation payloads."""

    candidate_id: UUID
    display_path: str = Field(min_length=1, max_length=2_000)
    object_type: ResidualObjectType
    size_bytes: int = Field(ge=0)
    classification: ResidualClassification
    ownership_confidence: OwnershipConfidence
    evidence_codes: tuple[str, ...] = Field(default=(), max_length=20)
    protection_level: UserDataProtectionLevel
    risk_flags: tuple[str, ...] = Field(default=(), max_length=20)


class ResidualModelPayload(FrozenModel):
    """Provider-neutral report explanation payload with no file contents."""

    report_id: UUID
    status: ResidualAnalysisStatus
    summary: ResidualReportSummary
    candidates: tuple[ResidualModelCandidate, ...] = Field(default=(), max_length=25_000)
    deletion_performed: bool = False

    @model_validator(mode="after")
    def enforce_read_only_payload(self) -> Self:
        """Prevent a provider payload from claiming destructive execution."""
        if self.deletion_performed:
            raise ValueError("Residual provider payload cannot authorize deletion")
        return self


class ResidualAnalyzeRequest(FrozenModel):
    """Validated request for one confirmed, context-bound R0 analysis."""

    context_id: UUID
    context_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_id: UUID
    max_objects: int = Field(default=25_000, ge=1, le=25_000)
    timeout_seconds: float = Field(default=60.0, gt=0, le=600.0)


class ResidualAnalyzeResult(FrozenModel):
    """Output of ``software.residuals.analyze``."""

    report: ResidualReport


class ResidualReportRequest(FrozenModel):
    """Request the latest report within one already confirmed context."""

    context_id: UUID


class ResidualReportResult(FrozenModel):
    """Latest persisted report, or an explicit absence."""

    report: ResidualReport | None


class ResidualInspectRequest(FrozenModel):
    """Inspect one exact candidate by opaque ID inside its context."""

    context_id: UUID
    candidate_id: UUID


class ResidualInspectResult(FrozenModel):
    """Exact candidate metadata, or an explicit absence."""

    candidate: ResidualCandidate | None


def _redacted_model_path(path: Path) -> str:
    raw = os.path.abspath(os.fspath(path))
    mappings = (
        ("LOCALAPPDATA", os.environ.get("LOCALAPPDATA")),
        ("APPDATA", os.environ.get("APPDATA")),
        ("USERPROFILE", os.environ.get("USERPROFILE")),
        ("PROGRAMDATA", os.environ.get("PROGRAMDATA")),
        ("PROGRAMFILES_X86", os.environ.get("PROGRAMFILES(X86)")),
        ("PROGRAMFILES", os.environ.get("PROGRAMFILES")),
    )
    normalized = os.path.normcase(raw)
    resolved_mappings = sorted(
        ((label, os.path.abspath(root)) for label, root in mappings if root is not None),
        key=lambda item: len(item[1]),
        reverse=True,
    )
    for label, resolved_root in resolved_mappings:
        normalized_root = os.path.normcase(resolved_root).rstrip("\\/")
        if normalized == normalized_root:
            return f"%{label}%"
        for separator in ("\\", "/"):
            prefix = f"{normalized_root}{separator}"
            if normalized.startswith(prefix):
                relative = raw[len(normalized_root) :].lstrip("\\/")
                display_relative = relative.replace("\\", "/")
                return f"%{label}%/{display_relative}"
    return f"<LOCAL_PATH>/{path.name or 'root'}"
