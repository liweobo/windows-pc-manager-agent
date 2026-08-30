"""Background workers for Stage 4E2 Fresh scans and controlled execution."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.app.runtime import ApplicationRuntime, SystemCleanupServices
from pc_manager_agent.domain.system_cleanup_execution import (
    CleanupResult,
    RecycleBinEmptyResult,
    SystemCleanupAssessment,
    SystemCleanupRequest,
)
from pc_manager_agent.orchestration.system_cleanup import (
    PreparedRecycleBinEmpty,
    PreparedSystemCleanup,
    RuntimeRecycleBinEmpty,
    RuntimeSystemCleanup,
)
from pc_manager_agent.tools.manifest import CancellationToken


class SystemCleanupWorkerSignals(QObject):
    """Emit one validated result or a user-facing failure string."""

    completed = Signal(object)
    failed = Signal(str)


@dataclass(frozen=True, slots=True)
class AssessedSystemCleanup:
    """Keep the isolated registry paired with its Fresh assessment."""

    services: SystemCleanupServices
    assessment: SystemCleanupAssessment


@dataclass(frozen=True, slots=True)
class PreparedEmptyCleanup:
    """Keep the isolated registry paired with an irreversible empty plan."""

    services: SystemCleanupServices
    prepared: PreparedRecycleBinEmpty


class _CancellableWorker(QRunnable):
    """Shared cooperative-cancellation plumbing for local Stage 4E2 work."""

    def __init__(self) -> None:
        super().__init__()
        self.signals = SystemCleanupWorkerSignals()
        self.cancellation = CancellationToken()

    def cancel(self) -> None:
        """Request cancellation without terminating a Windows Shell call."""
        self.cancellation.cancel()


class SystemCleanupAssessmentWorker(_CancellableWorker):
    """Create the isolated services and perform the first Fresh metadata scan."""

    def __init__(self, runtime: ApplicationRuntime, request: SystemCleanupRequest) -> None:
        super().__init__()
        self._runtime = runtime
        self._request = request

    @Slot()
    def run(self) -> None:
        """Return complete eligible and blocked rows without changing files."""
        try:
            services = self._runtime.create_system_cleanup_services()
            assessment = services.service.assess(self._request, self.cancellation)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(AssessedSystemCleanup(services, assessment))


class SystemCleanupRuntimeWorker(_CancellableWorker):
    """Repeat exact selected-item evidence before immediate confirmation."""

    def __init__(
        self,
        services: SystemCleanupServices,
        prepared: PreparedSystemCleanup,
    ) -> None:
        super().__init__()
        self._services = services
        self._prepared = prepared

    @Slot()
    def run(self) -> None:
        """Return a new bound Preview or fail closed."""
        try:
            value = self._services.service.request_runtime_confirmation(
                self._prepared,
                self.cancellation,
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(value)


class SystemCleanupExecuteWorker(_CancellableWorker):
    """Execute a consumed item-cleanup capability sequentially."""

    def __init__(
        self,
        services: SystemCleanupServices,
        runtime: RuntimeSystemCleanup,
    ) -> None:
        super().__init__()
        self._services = services
        self._runtime = runtime

    @Slot()
    def run(self) -> None:
        """Stop future items on cancellation or failure and emit the truthful result."""
        try:
            value = self._services.service.execute(self._runtime, self.cancellation)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(value)


class RecycleBinEmptyPrepareWorker(_CancellableWorker):
    """Inspect the exact system volume and create an independent plan."""

    def __init__(self, runtime: ApplicationRuntime) -> None:
        super().__init__()
        self._runtime = runtime

    @Slot()
    def run(self) -> None:
        """Return only complete non-empty inventory evidence."""
        try:
            services = self._runtime.create_system_cleanup_services()
            if self.cancellation.cancellation_requested():
                raise RuntimeError("Recycle Bin inspection cancelled")
            prepared = services.service.prepare_recycle_bin_empty()
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(PreparedEmptyCleanup(services, prepared))


class RecycleBinEmptyRuntimeWorker(_CancellableWorker):
    """Freshly reinspect the exact inventory before immediate confirmation."""

    def __init__(
        self,
        services: SystemCleanupServices,
        prepared: PreparedRecycleBinEmpty,
    ) -> None:
        super().__init__()
        self._services = services
        self._prepared = prepared

    @Slot()
    def run(self) -> None:
        """Return a new irreversible Preview or fail closed on any change."""
        try:
            if self.cancellation.cancellation_requested():
                raise RuntimeError("Recycle Bin reinspection cancelled")
            value = self._services.service.request_empty_runtime_confirmation(self._prepared)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(value)


class RecycleBinEmptyExecuteWorker(_CancellableWorker):
    """Execute one separately confirmed exact-volume empty action."""

    def __init__(
        self,
        services: SystemCleanupServices,
        runtime: RuntimeRecycleBinEmpty,
    ) -> None:
        super().__init__()
        self._services = services
        self._runtime = runtime

    @Slot()
    def run(self) -> None:
        """Emit a verified or unknown irreversible result; never retry."""
        try:
            value = self._services.service.execute_recycle_bin_empty(
                self._runtime,
                self.cancellation,
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(value)


def require_assessment(value: object) -> AssessedSystemCleanup:
    """Narrow a Qt payload before the dialog consumes it."""
    if not isinstance(value, AssessedSystemCleanup):
        raise TypeError("Fresh cleanup assessment returned an invalid value")
    return value


def require_runtime_cleanup(value: object) -> RuntimeSystemCleanup:
    """Narrow a Qt payload to a runtime item Preview."""
    if not isinstance(value, RuntimeSystemCleanup):
        raise TypeError("Runtime cleanup Preview returned an invalid value")
    return value


def require_cleanup_result(value: object) -> CleanupResult:
    """Narrow a Qt payload to a terminal item result."""
    if not isinstance(value, CleanupResult):
        raise TypeError("Cleanup execution returned an invalid value")
    return value


def require_prepared_empty(value: object) -> PreparedEmptyCleanup:
    """Narrow a Qt payload to an independent empty plan."""
    if not isinstance(value, PreparedEmptyCleanup):
        raise TypeError("Recycle Bin inspection returned an invalid value")
    return value


def require_runtime_empty(value: object) -> RuntimeRecycleBinEmpty:
    """Narrow a Qt payload to an immediate empty Preview."""
    if not isinstance(value, RuntimeRecycleBinEmpty):
        raise TypeError("Recycle Bin runtime Preview returned an invalid value")
    return value


def require_empty_result(value: object) -> RecycleBinEmptyResult:
    """Narrow a Qt payload to an irreversible empty result."""
    if not isinstance(value, RecycleBinEmptyResult):
        raise TypeError("Recycle Bin emptying returned an invalid value")
    return value
