"""Exclusive-create CSV and JSON export for measured analysis candidates."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from pc_manager_agent.domain.file_analysis import (
    FileAnalysisPlan,
    FileAnalysisReport,
    StoredFileRecord,
)
from pc_manager_agent.persistence.analysis_results import AnalysisResultRepository
from pc_manager_agent.platform_support.windows.path_info import is_network_path


class ReportFormat(StrEnum):
    """Supported local report encodings."""

    CSV = "csv"
    JSON = "json"


class ReportExportError(RuntimeError):
    """Raised when a report cannot be created without overwriting data."""


class ReportExportResult(BaseModel):
    """Verified outcome of one explicit report creation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    format: ReportFormat
    rows: int = Field(ge=0)
    size_bytes: int = Field(ge=0)


class ReportExporter:
    """Create a new local report and categorically refuse existing targets."""

    def __init__(
        self,
        results: AnalysisResultRepository,
        *,
        on_export: Callable[[ReportExportResult], None] | None = None,
    ) -> None:
        self._results = results
        self._on_export = on_export

    def export(
        self,
        target: Path,
        format: ReportFormat,
        plan: FileAnalysisPlan,
        report: FileAnalysisReport,
    ) -> ReportExportResult:
        """Write a bounded-memory report after validating the user-selected target."""
        canonical_target = self._validate_target(target, format)
        rows = 0
        try:
            if format is ReportFormat.CSV:
                rows = self._write_csv(canonical_target, plan, report)
            else:
                rows = self._write_json(canonical_target, plan, report)
        except Exception as exc:
            # Stage 1 never deletes a file, including an incomplete report it just
            # created. The exclusive-create target makes ownership unambiguous and
            # the error tells the user which partial artifact to inspect manually.
            raise ReportExportError(
                f"Report creation failed; an incomplete file may remain at {canonical_target}"
            ) from exc
        result = ReportExportResult(
            path=canonical_target,
            format=format,
            rows=rows,
            size_bytes=canonical_target.stat().st_size,
        )
        if self._on_export is not None:
            self._on_export(result)
        return result

    @staticmethod
    def _validate_target(target: Path, format: ReportFormat) -> Path:
        if not target.is_absolute():
            raise ReportExportError("Report path must be absolute")
        if ".." in target.parts or any(part.rstrip(" .") != part for part in target.parts):
            raise ReportExportError("Report path is ambiguous")
        if target.exists():
            raise ReportExportError("Existing files are never overwritten")
        if not target.parent.is_dir():
            raise ReportExportError("Report destination directory does not exist")
        if is_network_path(target):
            raise ReportExportError("Network report destinations are unavailable in Stage 1")
        expected_suffix = f".{format.value}"
        if target.suffix.casefold() != expected_suffix:
            raise ReportExportError(f"Report filename must end with {expected_suffix}")
        return target

    def _write_csv(
        self,
        target: Path,
        plan: FileAnalysisPlan,
        report: FileAnalysisReport,
    ) -> int:
        fieldnames = [
            "scan_completed_at",
            "scan_roots",
            "minimum_size_bytes",
            "inactive_days",
            "path",
            "name",
            "extension",
            "size_bytes",
            "category",
            "created_at",
            "modified_at",
            "accessed_at",
            "hidden",
            "read_only",
            "possibly_inactive",
            "inactive_confidence",
            "inactive_evidence",
            "duplicate_group_id",
        ]
        rows = 0
        with target.open("x", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for batch in self._results.iter_matching(report.analysis_session_id):
                for record in batch:
                    writer.writerow(self._row(record, plan, report))
                    rows += 1
        return rows

    def _write_json(
        self,
        target: Path,
        plan: FileAnalysisPlan,
        report: FileAnalysisReport,
    ) -> int:
        rows = 0
        with target.open("x", encoding="utf-8", newline="") as handle:
            header = {
                "scan_completed_at": report.completed_at.isoformat(),
                "scan_roots": [str(path) for path in plan.task_plan.scope.included_paths],
                "filters": plan.filters.model_dump(mode="json"),
                "analyses": [item.value for item in plan.analyses],
                "summary": report.summary.model_dump(mode="json"),
                "files": [],
            }
            prefix = json.dumps(header, ensure_ascii=False, separators=(",", ":"))
            marker = '"files":[]'
            before, after = prefix.split(marker, maxsplit=1)
            handle.write(before)
            handle.write('"files":[')
            first = True
            for batch in self._results.iter_matching(report.analysis_session_id):
                for record in batch:
                    if not first:
                        handle.write(",")
                    json.dump(
                        self._row(record, plan, report),
                        handle,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    first = False
                    rows += 1
            handle.write("]")
            handle.write(after)
        return rows

    @staticmethod
    def _row(
        record: StoredFileRecord,
        plan: FileAnalysisPlan,
        report: FileAnalysisReport,
    ) -> dict[str, object]:
        metadata = record.metadata
        inactive = record.inactive
        return {
            "scan_completed_at": report.completed_at.isoformat(),
            "scan_roots": " | ".join(str(path) for path in plan.task_plan.scope.included_paths),
            "minimum_size_bytes": plan.filters.minimum_size_bytes,
            "inactive_days": plan.filters.inactive_days,
            "path": str(metadata.path),
            "name": metadata.name,
            "extension": metadata.extension,
            "size_bytes": metadata.size_bytes,
            "category": metadata.category.value,
            "created_at": metadata.created_at.isoformat(),
            "modified_at": metadata.modified_at.isoformat(),
            "accessed_at": metadata.accessed_at.isoformat(),
            "hidden": metadata.hidden,
            "read_only": metadata.read_only,
            "possibly_inactive": bool(inactive and inactive.possibly_inactive),
            "inactive_confidence": inactive.confidence.value if inactive else None,
            "inactive_evidence": " | ".join(inactive.evidence) if inactive else "",
            "duplicate_group_id": record.duplicate_group_id,
        }
