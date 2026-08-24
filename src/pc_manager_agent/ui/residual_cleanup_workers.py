"""Qt workers for fresh Stage 4D4 assessment, confirmation, and execution."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.app.runtime import ApplicationRuntime, ResidualCleanupServices
from pc_manager_agent.domain.residual_cleanup import (
    ResidualCleanupAssessment,
    ResidualCleanupExecutionReport,
    ResidualCleanupRequest,
)
from pc_manager_agent.orchestration.residual_cleanup import (
    PreparedResidualCleanup,
    RuntimeResidualCleanup,
)
from pc_manager_agent.tools.manifest import CancellationToken


class ResidualCleanupWorkerSignals(QObject):
    """Terminal signals shared by Stage 4D4 background workers."""

    completed = Signal(object)
    failed = Signal(str)


@dataclass(frozen=True, slots=True)
class PreparedResidualCleanupUiState:
    """Keep services, fresh assessment, and optional executable plan together."""

    services: ResidualCleanupServices
    assessment: ResidualCleanupAssessment
    prepared: PreparedResidualCleanup | None


class ResidualCleanupPrepareWorker(QRunnable):
    """Run Fresh Revalidation and compile only an all-eligible batch off the UI thread."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        request: ResidualCleanupRequest,
    ) -> None:
        super().__init__()
        self.signals = ResidualCleanupWorkerSignals()
        self.cancellation = CancellationToken()
        self._runtime = runtime
        self._request = request

    @Slot()
    def run(self) -> None:
        """Emit blocked assessment rows or a pending plan confirmation."""
        try:
            services = self._runtime.create_residual_cleanup_services()
            assessment = services.service.assess(self._request, self.cancellation)
            prepared = services.service.prepare(assessment) if assessment.all_eligible else None
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(PreparedResidualCleanupUiState(services, assessment, prepared))

    def cancel(self) -> None:
        """Request cancellation of future metadata traversal."""
        self.cancellation.cancel()


class ResidualCleanupRuntimeWorker(QRunnable):
    """Repeat the full scan before issuing the immediate confirmation."""

    def __init__(
        self,
        services: ResidualCleanupServices,
        prepared: PreparedResidualCleanup,
    ) -> None:
        super().__init__()
        self.signals = ResidualCleanupWorkerSignals()
        self.cancellation = CancellationToken()
        self._services = services
        self._prepared = prepared

    @Slot()
    def run(self) -> None:
        """Emit a separately rescanned runtime Preview or a safe failure."""
        try:
            runtime = self._services.service.request_runtime_confirmation(
                self._prepared,
                self.cancellation,
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(runtime)

    def cancel(self) -> None:
        """Stop the fresh scan before any write becomes possible."""
        self.cancellation.cancel()


class ResidualCleanupExecuteWorker(QRunnable):
    """Run the sequential Recycle Bin transaction without blocking the GUI."""

    def __init__(
        self,
        services: ResidualCleanupServices,
        runtime: RuntimeResidualCleanup,
    ) -> None:
        super().__init__()
        self.signals = ResidualCleanupWorkerSignals()
        self.cancellation = CancellationToken()
        self._services = services
        self._runtime = runtime

    @Slot()
    def run(self) -> None:
        """Emit a truthful completed, partial, failed, or cancelled report."""
        try:
            report = self._services.service.execute(self._runtime, self.cancellation)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(report)

    def cancel(self) -> None:
        """Stop future items; an in-flight Shell operation remains atomic."""
        self.cancellation.cancel()


def require_cleanup_prepared(value: object) -> PreparedResidualCleanupUiState:
    """Narrow one Qt payload to Fresh Revalidation UI state."""
    if not isinstance(value, PreparedResidualCleanupUiState):
        raise TypeError("worker emitted invalid residual cleanup preparation state")
    return value


def require_cleanup_runtime(value: object) -> RuntimeResidualCleanup:
    """Narrow one Qt payload to a pending immediate confirmation."""
    if not isinstance(value, RuntimeResidualCleanup):
        raise TypeError("worker emitted invalid residual cleanup runtime state")
    return value


def require_cleanup_report(value: object) -> ResidualCleanupExecutionReport:
    """Narrow one Qt payload to a terminal cleanup report."""
    if not isinstance(value, ResidualCleanupExecutionReport):
        raise TypeError("worker emitted invalid residual cleanup report")
    return value
