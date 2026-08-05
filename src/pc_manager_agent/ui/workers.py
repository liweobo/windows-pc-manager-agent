"""Qt worker objects for non-blocking deterministic tool execution."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.domain.plans import TaskPlan
from pc_manager_agent.domain.reports import ScanReport
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
