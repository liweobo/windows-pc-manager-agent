"""Typed models shared by the read-only scanner and analysis pipeline."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReportModel(BaseModel):
    """Immutable, strict report model."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class FileCategory(StrEnum):
    """Stable user-facing file categories used by every analysis component."""

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    PDF = "pdf"
    ARCHIVE = "archive"
    INSTALLER = "installer"
    DISK_IMAGE = "disk_image"
    CODE = "code"
    DATABASE = "database"
    BACKUP = "backup"
    OTHER = "other"


class ScanStatus(StrEnum):
    """Terminal state of a bounded scan session."""

    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    TRUNCATED = "TRUNCATED"
    FAILED = "FAILED"


class ScanRequest(ReportModel):
    """Validated input for the metadata-only directory scanner."""

    root: Path
    excluded_paths: tuple[Path, ...] = ()
    max_files: int = Field(default=50_000, ge=1, le=100_000)
    timeout_seconds: float = Field(default=300.0, gt=0, le=3_600)
    session_id: UUID | None = None
    batch_size: int = Field(default=250, ge=1, le=2_000)
    retain_files: bool = True

    @model_validator(mode="after")
    def require_session_for_streaming(self) -> ScanRequest:
        """Require a session identifier when results are not returned in memory."""
        if not self.retain_files and self.session_id is None:
            raise ValueError("session_id is required when retain_files is false")
        return self


class FileMetadata(ReportModel):
    """Metadata gathered without reading file contents."""

    path: Path
    name: str
    extension: str
    media_type: str | None
    size_bytes: int = Field(ge=0)
    created_at: datetime
    modified_at: datetime
    accessed_at: datetime
    scan_root: Path
    hidden: bool = False
    read_only: bool = False
    system: bool = False
    offline: bool = False
    category: FileCategory = FileCategory.OTHER
    file_id: int | None = Field(default=None, ge=0)
    device_id: int | None = Field(default=None, ge=0)
    windows_attributes: int | None = Field(default=None, ge=0)


class ScanIssue(ReportModel):
    """A skipped object or non-fatal scanner error."""

    code: str
    message: str
    path: Path | None = None


class ScanBatch(ReportModel):
    """One bounded scanner batch delivered to an injected result sink."""

    session_id: UUID
    files: tuple[FileMetadata, ...]


class ScanProgress(ReportModel):
    """Thread-safe progress payload that never claims an unknown percentage."""

    session_id: UUID | None = None
    files_seen: int = Field(ge=0)
    directories_seen: int = Field(ge=0)
    total_size_bytes: int = Field(ge=0)
    issues: int = Field(ge=0)
    phase: str = "scanning"
    completed_units: int | None = Field(default=None, ge=0)
    total_units: int | None = Field(default=None, ge=0)


class ScanSummary(ReportModel):
    """Aggregate scanner outcome."""

    files_seen: int = Field(ge=0)
    directories_seen: int = Field(ge=0)
    total_size_bytes: int = Field(ge=0)
    issues: int = Field(ge=0)
    cancelled: bool = False
    timed_out: bool = False
    truncated: bool = False
    duration_ms: int = Field(ge=0)
    status: ScanStatus = ScanStatus.COMPLETED


class ScanReport(ReportModel):
    """Complete metadata-only directory scan report."""

    root: Path
    files: tuple[FileMetadata, ...]
    issues: tuple[ScanIssue, ...]
    summary: ScanSummary
    session_id: UUID | None = None
