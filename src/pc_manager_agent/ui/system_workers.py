"""Qt worker for confirmed non-blocking system diagnostic collection."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.domain.system_diagnostics import DiagnosticPlan, DiagnosticReport
from pc_manager_agent.orchestration.system_diagnostics import DiagnosticOrchestrator
from pc_manager_agent.tools.manifest import CancellationToken


class DiagnosticWorkerSignals(QObject):
    """Thread-safe terminal signals for a diagnostic worker."""

    completed = Signal(object)
    failed = Signal(str)


class DiagnosticWorker(QRunnable):
    """Execute one already-confirmed read-only plan outside the GUI thread."""

    def __init__(self, orchestrator: DiagnosticOrchestrator, plan: DiagnosticPlan) -> None:
        super().__init__()
        self.signals = DiagnosticWorkerSignals()
        self.cancellation = CancellationToken()
        self._orchestrator = orchestrator
        self._plan = plan

    @Slot()
    def run(self) -> None:
        """Run the workflow and convert worker-boundary errors to safe text."""
        try:
            report = self._orchestrator.execute(self._plan, self.cancellation)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(report)

    def cancel(self) -> None:
        """Request cooperative cancellation of sampling and future collectors."""
        self.cancellation.cancel()


def require_diagnostic_report(value: object) -> DiagnosticReport:
    """Narrow an object transported through a Qt signal."""
    if not isinstance(value, DiagnosticReport):
        raise TypeError("Worker emitted an invalid diagnostic report")
    return value
