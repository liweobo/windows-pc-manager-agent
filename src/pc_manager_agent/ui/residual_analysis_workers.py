"""Qt workers for Stage 4D3 plan preparation and bounded metadata analysis."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.app.runtime import ApplicationRuntime, ResidualAnalysisServices
from pc_manager_agent.domain.plans import TaskPlan
from pc_manager_agent.domain.software_residuals import ResidualReport, UninstallContext
from pc_manager_agent.orchestration.software_residual_analysis import ResidualSafetyReview
from pc_manager_agent.reporting.residual_exporter import (
    ResidualExportResult,
    ResidualReportFormat,
)
from pc_manager_agent.tools.manifest import CancellationToken


class ResidualWorkerSignals(QObject):
    """Terminal signals shared by preparation and analysis workers."""

    completed = Signal(object)
    failed = Signal(str)


@dataclass(frozen=True, slots=True)
class PreparedResidualAnalysis:
    """Keep the dependency graph and exact reviewed context together."""

    services: ResidualAnalysisServices
    plan: TaskPlan
    context: UninstallContext
    review: ResidualSafetyReview


class ResidualPrepareWorker(QRunnable):
    """Load context and compile the exact R0 plan off the UI thread."""

    def __init__(self, runtime: ApplicationRuntime, user_goal: str, transaction_id: UUID) -> None:
        super().__init__()
        self.signals = ResidualWorkerSignals()
        self._runtime = runtime
        self._user_goal = user_goal
        self._transaction_id = transaction_id

    @Slot()
    def run(self) -> None:
        """Emit a reviewed plan or a user-safe failure."""
        try:
            services = self._runtime.create_residual_analysis_services()
            plan, context, review = services.service.prepare(self._user_goal, self._transaction_id)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(PreparedResidualAnalysis(services, plan, context, review))


class ResidualAnalyzeWorker(QRunnable):
    """Run the bounded metadata scan with cooperative cancellation."""

    def __init__(
        self,
        services: ResidualAnalysisServices,
        plan: TaskPlan,
        context: UninstallContext,
    ) -> None:
        super().__init__()
        self.signals = ResidualWorkerSignals()
        self.cancellation = CancellationToken()
        self._services = services
        self._plan = plan
        self._context = context

    @Slot()
    def run(self) -> None:
        """Emit a complete, partial, truncated, or cancelled report."""
        try:
            report = self._services.service.analyze(self._plan, self._context, self.cancellation)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(report)

    def cancel(self) -> None:
        """Request cancellation without modifying any scanned object."""
        self.cancellation.cancel()


class ResidualExportWorker(QRunnable):
    """Create and audit one local report off the UI thread."""

    def __init__(
        self,
        services: ResidualAnalysisServices,
        plan: TaskPlan,
        context: UninstallContext,
        report: ResidualReport,
        target: Path,
        format: ResidualReportFormat,
    ) -> None:
        super().__init__()
        self.signals = ResidualWorkerSignals()
        self._services = services
        self._plan = plan
        self._context = context
        self._report = report
        self._target = target
        self._format = format

    @Slot()
    def run(self) -> None:
        """Emit the bounded export result or a user-safe failure."""
        try:
            result = self._services.service.export_report(
                self._plan,
                self._context,
                self._report,
                self._target,
                self._format,
                self._services.exporter,
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(result)


def require_prepared_residual(value: object) -> PreparedResidualAnalysis:
    """Narrow one Qt payload to a reviewed Stage 4D3 plan."""
    if not isinstance(value, PreparedResidualAnalysis):
        raise TypeError("worker emitted invalid residual preparation state")
    return value


def require_residual_report(value: object) -> ResidualReport:
    """Narrow one Qt payload to a Stage 4D3 report."""
    if not isinstance(value, ResidualReport):
        raise TypeError("worker emitted invalid residual report")
    return value


def require_residual_export(value: object) -> ResidualExportResult:
    """Narrow one Qt payload to a completed Stage 4D3 export."""
    if not isinstance(value, ResidualExportResult):
        raise TypeError("worker emitted invalid residual export result")
    return value
