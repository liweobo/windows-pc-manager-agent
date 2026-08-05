"""Read-only file scanning request and report models."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ReportModel(BaseModel):
    """Immutable, strict report model."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ScanRequest(ReportModel):
    """Validated input for the metadata-only directory scanner."""

    root: Path
    excluded_paths: tuple[Path, ...] = ()
    max_files: int = Field(default=50_000, ge=1, le=100_000)
    timeout_seconds: float = Field(default=300.0, gt=0, le=3_600)


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


class ScanIssue(ReportModel):
    """A skipped object or non-fatal scanner error."""

    code: str
    message: str
    path: Path | None = None


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


class ScanReport(ReportModel):
    """Complete metadata-only directory scan report."""

    root: Path
    files: tuple[FileMetadata, ...]
    issues: tuple[ScanIssue, ...]
    summary: ScanSummary
