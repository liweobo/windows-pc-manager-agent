"""Exclusive-create JSON/CSV export for a Stage 4E1 report."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from pc_manager_agent.domain.system_optimization import SystemOptimizationReport
from pc_manager_agent.platform_support.windows.path_info import is_network_path
from pc_manager_agent.reporting.exporter import ReportExportError, ReportExportResult, ReportFormat


class SystemOptimizationReportExporter:
    """Create a user-requested report file without overwrite or execution authority."""

    def export(
        self,
        target: Path,
        format: ReportFormat,
        report: SystemOptimizationReport,
    ) -> ReportExportResult:
        """Export the current in-memory report using exclusive file creation."""
        validated = self._validate_target(target, format)
        try:
            rows = (
                self._write_csv(validated, report)
                if format is ReportFormat.CSV
                else self._write_json(validated, report)
            )
        except Exception as exc:
            raise ReportExportError(
                f"Report creation failed; an incomplete file may remain at {validated}"
            ) from exc
        return ReportExportResult(
            path=validated,
            format=format,
            rows=rows,
            size_bytes=validated.stat().st_size,
        )

    @staticmethod
    def _validate_target(target: Path, format: ReportFormat) -> Path:
        if not target.is_absolute() or ".." in target.parts:
            raise ReportExportError("Report path must be absolute and traversal-free")
        if any(part.rstrip(" .") != part for part in target.parts):
            raise ReportExportError("Report path is ambiguous")
        if target.exists():
            raise ReportExportError("Existing files are never overwritten")
        if not target.parent.is_dir() or is_network_path(target):
            raise ReportExportError("Report destination must be an existing local directory")
        if target.suffix.casefold() != f".{format.value}":
            raise ReportExportError(f"Report filename must end with .{format.value}")
        return target

    @staticmethod
    def _write_json(target: Path, report: SystemOptimizationReport) -> int:
        with target.open("x", encoding="utf-8", newline="") as handle:
            json.dump(report.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)
        return len(report.cleanup_candidates)

    @staticmethod
    def _write_csv(target: Path, report: SystemOptimizationReport) -> int:
        fields = (
            "candidate_id",
            "category",
            "source",
            "path",
            "observed_size_bytes",
            "potential_reclaim_bytes",
            "item_count",
            "safety_classification",
            "protection_level",
            "confidence",
            "recoverability",
            "reason_codes",
            "stage4e1_executable",
        )
        with target.open("x", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for item in report.cleanup_candidates:
                writer.writerow(
                    {
                        "candidate_id": str(item.candidate_id),
                        "category": item.category.value,
                        "source": item.source,
                        "path": str(item.path) if item.path else "",
                        "observed_size_bytes": item.observed_size_bytes,
                        "potential_reclaim_bytes": item.potential_reclaim_bytes,
                        "item_count": item.item_count,
                        "safety_classification": item.safety_classification.value,
                        "protection_level": item.protection_level.value,
                        "confidence": item.confidence.value,
                        "recoverability": item.recoverability.value,
                        "reason_codes": " | ".join(code.value for code in item.reason_codes),
                        "stage4e1_executable": False,
                    }
                )
        return len(report.cleanup_candidates)
