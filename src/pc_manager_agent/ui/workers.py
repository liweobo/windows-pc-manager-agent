"""Qt worker objects for non-blocking deterministic tool execution."""

from __future__ import annotations

import asyncio
from uuid import UUID

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.file_analysis import FileAnalysisPlan, FileAnalysisReport
from pc_manager_agent.domain.plans import TaskPlan
from pc_manager_agent.domain.reports import ScanReport
from pc_manager_agent.orchestration.explanation import FileAnalysisExplainer
from pc_manager_agent.orchestration.file_analysis_planner import (
    FileAnalysisPlanner,
    FileAnalysisPlanningResult,
)
from pc_manager_agent.orchestration.service import ScanOrchestrator
from pc_manager_agent.tools.manifest import CancellationToken


class ScanWorkerSignals(QObject):
    """Thread-safe signals emitted by a scan worker."""

    completed = Signal(object)
    failed = Signal(str)


class ScanWorker(QRunnable):
    """Execute one already-confirmed scan outside the GUI thread."""

    def __init__(self, orchestrator: ScanOrchestrator, plan: TaskPlan) -> None:
        super().__init__()
        self.signals = ScanWorkerSignals()
        self.cancellation = CancellationToken()
        self._orchestrator = orchestrator
        self._plan = plan

    @Slot()
    def run(self) -> None:
        """Execute and convert exceptions to a user-safe signal."""
        try:
            report = self._orchestrator.execute(self._plan, self.cancellation)
        except Exception as exc:  # Qt worker boundary must report all failures.
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(report)

    def cancel(self) -> None:
        """Request cooperative cancellation."""
        self.cancellation.cancel()


def require_scan_report(value: object) -> ScanReport:
    """Narrow an object transported through a Qt signal."""
    if not isinstance(value, ScanReport):
        msg = "Worker emitted an invalid scan report"
        raise TypeError(msg)
    return value


class FileAnalysisWorkerSignals(QObject):
    """Thread-safe progress and terminal signals for a Stage 1 execution."""

    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(str)


class FileAnalysisWorker(QRunnable):
    """Build execution services and run one confirmed plan off the GUI thread."""

    def __init__(self, runtime: ApplicationRuntime, plan: FileAnalysisPlan) -> None:
        super().__init__()
        self.signals = FileAnalysisWorkerSignals()
        self.cancellation = CancellationToken()
        self._plan = plan
        self._services = runtime.create_file_analysis_services(self.signals.progress.emit)

    @Slot()
    def run(self) -> None:
        """Execute the confirmed plan and convert failures to a safe signal."""
        try:
            report = self._services.orchestrator.execute(
                self._plan,
                self.cancellation,
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(report)

    def cancel(self) -> None:
        """Request cooperative cancellation."""
        self.cancellation.cancel()


class PlannerWorkerSignals(QObject):
    """Terminal signals for an asynchronous provider planning call."""

    completed = Signal(object)
    failed = Signal(str)


class PlannerWorker(QRunnable):
    """Run an already-consented provider planning call outside the GUI thread."""

    def __init__(
        self,
        planner: FileAnalysisPlanner,
        user_goal: str,
        confirmation_id: UUID,
        root_ids: tuple[UUID, ...],
    ) -> None:
        super().__init__()
        self.signals = PlannerWorkerSignals()
        self._planner = planner
        self._user_goal = user_goal
        self._confirmation_id = confirmation_id
        self._root_ids = root_ids

    @Slot()
    def run(self) -> None:
        """Create a provider intent and compile it into a deterministic plan."""
        try:
            result = asyncio.run(
                self._planner.plan(
                    self._user_goal,
                    self._confirmation_id,
                    self._root_ids,
                )
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(result)


class ExplanationWorkerSignals(QObject):
    """Terminal signals for an aggregate-only provider explanation."""

    completed = Signal(str)
    failed = Signal(str)


class ExplanationWorker(QRunnable):
    """Request one already-consented aggregate explanation off the GUI thread."""

    def __init__(
        self,
        explainer: FileAnalysisExplainer,
        plan: FileAnalysisPlan,
        report: FileAnalysisReport,
        confirmation_id: UUID,
    ) -> None:
        super().__init__()
        self.signals = ExplanationWorkerSignals()
        self._explainer = explainer
        self._plan = plan
        self._report = report
        self._confirmation_id = confirmation_id

    @Slot()
    def run(self) -> None:
        """Return provider observations with deterministic numeric rendering."""
        try:
            text = asyncio.run(
                self._explainer.explain(
                    self._plan,
                    self._report.summary,
                    self._confirmation_id,
                )
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(text)


def require_analysis_report(value: object) -> FileAnalysisReport:
    """Narrow a Stage 1 report transported through a Qt signal."""
    if not isinstance(value, FileAnalysisReport):
        raise TypeError("Worker emitted an invalid file analysis report")
    return value


def require_planning_result(value: object) -> FileAnalysisPlanningResult:
    """Narrow a provider planning result transported through a Qt signal."""
    if not isinstance(value, FileAnalysisPlanningResult):
        raise TypeError("Worker emitted an invalid file analysis planning result")
    return value
