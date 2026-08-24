"""Exclusive-create local JSON and CSV export for Stage 4D3 reports."""

from __future__ import annotations

import csv
import json
import os
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from pc_manager_agent.domain.software_residuals import ResidualReport


class ResidualReportFormat(StrEnum):
    """Supported user-initiated local report formats."""

    JSON = "json"
    CSV = "csv"


class ResidualExportError(RuntimeError):
    """Raised when report export cannot preserve its no-overwrite boundary."""


class ResidualExportResult(BaseModel):
    """Metadata about one user-requested report file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: str
    target: Path
    format: ResidualReportFormat
    candidate_count: int = Field(ge=0)


class ResidualReportExporter:
    """Export local report data without overwriting or network destinations."""

    def export(
        self,
        report: ResidualReport,
        target: Path,
        format: ResidualReportFormat,
    ) -> ResidualExportResult:
        """Create one new JSON or CSV file and return its bounded summary."""
        validated = self._validate_target(target, format)
        try:
            if format is ResidualReportFormat.JSON:
                with validated.open("x", encoding="utf-8", newline="") as stream:
                    json.dump(
                        report.model_dump(mode="json"),
                        stream,
                        ensure_ascii=False,
                        indent=2,
                    )
                    stream.write("\n")
            else:
                with validated.open("x", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(
                        (
                            "path",
                            "object_type",
                            "size_bytes",
                            "classification",
                            "ownership_confidence",
                            "protection_level",
                            "recommendation",
                            "risk_flags",
                            "modified_at",
                        )
                    )
                    for candidate in report.candidates:
                        writer.writerow(
                            (
                                str(candidate.path),
                                candidate.object_type.value,
                                candidate.size_bytes,
                                candidate.classification.value,
                                candidate.ownership_confidence.value,
                                candidate.protection_level.value,
                                candidate.recommendation.value,
                                "|".join(candidate.risk_flags),
                                candidate.modified_at.isoformat()
                                if candidate.modified_at is not None
                                else "",
                            )
                        )
        except FileExistsError as exc:
            raise ResidualExportError("Existing report files are never overwritten") from exc
        except OSError as exc:
            raise ResidualExportError("Residual report could not be created") from exc
        return ResidualExportResult(
            report_id=str(report.report_id),
            target=validated,
            format=format,
            candidate_count=len(report.candidates),
        )

    @staticmethod
    def _validate_target(target: Path, format: ResidualReportFormat) -> Path:
        if not target.is_absolute():
            raise ResidualExportError("Report path must be absolute")
        if ".." in target.parts or any(
            part not in {target.anchor, target.drive} and part.rstrip(" .") != part
            for part in target.parts
        ):
            raise ResidualExportError("Report path is ambiguous")
        raw = os.fspath(target)
        if raw.startswith(("\\\\", "//")):
            raise ResidualExportError("Network report destinations are unavailable")
        candidate = Path(os.path.abspath(os.path.normpath(raw)))
        if candidate.exists():
            raise ResidualExportError("Existing report files are never overwritten")
        if not candidate.parent.is_dir():
            raise ResidualExportError("Report destination directory does not exist")
        if candidate.suffix.casefold() != f".{format.value}":
            raise ResidualExportError(f"Report filename must end with .{format.value}")
        return candidate
