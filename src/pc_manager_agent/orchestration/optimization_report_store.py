"""Bounded session-only storage for non-authoritative Stage 4E1 reports."""

from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from threading import RLock
from uuid import UUID

from pc_manager_agent.domain.system_optimization import SystemOptimizationReport


class OptimizationReportUnavailableError(LookupError):
    """Raised when an old or unknown report cannot seed Fresh cleanup analysis."""


class OptimizationReportSessionStore:
    """Keep a small report set in memory so restart always invalidates old intent."""

    def __init__(self, *, ttl_seconds: int = 1_800, max_reports: int = 10) -> None:
        if ttl_seconds <= 0 or max_reports <= 0:
            raise ValueError("Optimization report session limits must be positive")
        self._ttl = timedelta(seconds=ttl_seconds)
        self._max_reports = max_reports
        self._reports: OrderedDict[UUID, SystemOptimizationReport] = OrderedDict()
        self._lock = RLock()

    def save(self, report: SystemOptimizationReport) -> None:
        """Retain one immutable report as intent only, never as write authority."""
        if report.stage4e1_executable or report.changes_performed:
            raise ValueError("An executable Stage 4E1 report cannot enter the session store")
        with self._lock:
            self._purge_expired(datetime.now(UTC))
            self._reports[report.report_id] = report
            self._reports.move_to_end(report.report_id)
            while len(self._reports) > self._max_reports:
                self._reports.popitem(last=False)

    def get(self, report_id: UUID) -> SystemOptimizationReport:
        """Resolve an unexpired local report without accepting a caller-supplied copy."""
        current = datetime.now(UTC)
        with self._lock:
            self._purge_expired(current)
            try:
                report = self._reports[report_id]
            except KeyError as exc:
                raise OptimizationReportUnavailableError(
                    "Stage 4E1 report is unavailable or expired; run a new analysis"
                ) from exc
            self._reports.move_to_end(report_id)
            return report

    def clear(self) -> None:
        """Invalidate every report intent during controlled shutdown."""
        with self._lock:
            self._reports.clear()

    def _purge_expired(self, now: datetime) -> None:
        expired = tuple(
            report_id
            for report_id, report in self._reports.items()
            if now >= report.generated_at + self._ttl
        )
        for report_id in expired:
            self._reports.pop(report_id, None)
