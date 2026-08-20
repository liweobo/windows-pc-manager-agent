"""Qt workers for Stage 4D1 plan preparation and read-only Preview analysis."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.app.runtime import ApplicationRuntime, SoftwareAnalysisServices
from pc_manager_agent.domain.software_uninstall_analysis import (
    SoftwareAnalysisOutcome,
    SoftwareTargetQuery,
    SoftwareUninstallAnalysisPlan,
)
from pc_manager_agent.safety.software_uninstall_validator import SoftwareSafetyReview
from pc_manager_agent.tools.manifest import CancellationToken


class SoftwareWorkerSignals(QObject):
    """Terminal signals shared by the finite Stage 4D1 workers."""

    completed = Signal(object)
    failed = Signal(str)


@dataclass(frozen=True, slots=True)
class PreparedSoftwareAnalysis:
    """Fresh services and one independently reviewed R0 plan."""

    services: SoftwareAnalysisServices
    plan: SoftwareUninstallAnalysisPlan
    review: SoftwareSafetyReview


class SoftwarePrepareWorker(QRunnable):
    """Create Stage 4D1 services and a local plan without blocking the GUI."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        user_goal: str,
        query: SoftwareTargetQuery | None = None,
    ) -> None:
        super().__init__()
        self.signals = SoftwareWorkerSignals()
        self._runtime = runtime
        self._user_goal = user_goal
        self._query = query

    @Slot()
    def run(self) -> None:
        """Build a plan and surface any fail-closed boundary to the UI."""
        try:
            services = self._runtime.create_software_analysis_services()
            plan, review = services.service.prepare(self._user_goal, self._query)
        except Exception as exc:  # Qt worker boundary converts failures to visible text.
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(PreparedSoftwareAnalysis(services, plan, review))


class SoftwareAnalyzeWorker(QRunnable):
    """Execute only the confirmed five-tool R0 analysis chain."""

    def __init__(
        self,
        services: SoftwareAnalysisServices,
        plan: SoftwareUninstallAnalysisPlan,
    ) -> None:
        super().__init__()
        self.signals = SoftwareWorkerSignals()
        self.cancellation = CancellationToken()
        self._services = services
        self._plan = plan

    @Slot()
    def run(self) -> None:
        """Return candidates or a non-executable Preview."""
        try:
            outcome = self._services.service.analyze(self._plan, self.cancellation)
        except Exception as exc:  # Qt worker boundary converts failures to visible text.
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(outcome)

    def cancel(self) -> None:
        """Request cooperative cancellation of remaining read-only probes."""
        self.cancellation.cancel()


def require_prepared_software_analysis(value: object) -> PreparedSoftwareAnalysis:
    """Narrow one Qt signal payload to prepared Stage 4D1 state."""
    if not isinstance(value, PreparedSoftwareAnalysis):
        raise TypeError("Worker emitted invalid software analysis plan state")
    return value


def require_software_analysis_outcome(value: object) -> SoftwareAnalysisOutcome:
    """Narrow one Qt signal payload to a candidate/Preview outcome."""
    if not isinstance(value, SoftwareAnalysisOutcome):
        raise TypeError("Worker emitted an invalid software analysis outcome")
    return value
