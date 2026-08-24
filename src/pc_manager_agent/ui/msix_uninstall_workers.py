"""Qt workers for the finite Stage 4D2C2 MSIX uninstall workflow."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.app.runtime import ApplicationRuntime, MsixUninstallServices
from pc_manager_agent.domain.msix_uninstall import (
    MsixTargetQuery,
    MsixUninstallPlan,
    MsixUninstallPreview,
    MsixUninstallResult,
)
from pc_manager_agent.orchestration.msix_uninstall_execution import (
    PreparedMsixRuntimeConfirmation,
    PreparedMsixUninstall,
)
from pc_manager_agent.tools.manifest import CancellationToken


class MsixWorkerSignals(QObject):
    """Terminal signals shared by preparation and execution workers."""

    completed = Signal(object)
    failed = Signal(str)


@dataclass(frozen=True, slots=True)
class PreparedMsixWithServices:
    """Keep one fresh dependency graph with its prepared result."""

    services: MsixUninstallServices
    prepared: PreparedMsixUninstall


class MsixPrepareWorker(QRunnable):
    """Refresh package identity, type, dependencies, and preflight off the UI thread."""

    def __init__(self, runtime: ApplicationRuntime, user_goal: str, query: MsixTargetQuery) -> None:
        super().__init__()
        self.signals = MsixWorkerSignals()
        self.cancellation = CancellationToken()
        self._runtime = runtime
        self._user_goal = user_goal
        self._query = query

    @Slot()
    def run(self) -> None:
        """Emit an exact Preview or a fail-closed candidate result."""
        try:
            services = self._runtime.create_msix_uninstall_services()
            prepared = services.service.prepare(self._user_goal, self._query, self.cancellation)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(PreparedMsixWithServices(services, prepared))

    def cancel(self) -> None:
        """Cancel preparation before any write is dispatched."""
        self.cancellation.cancel()


class MsixRuntimePrepareWorker(QRunnable):
    """Repeat identity, dependency, and preflight checks before immediate approval."""

    def __init__(
        self,
        services: MsixUninstallServices,
        plan_confirmation_id: UUID,
        plan: MsixUninstallPlan,
        preview: MsixUninstallPreview,
    ) -> None:
        super().__init__()
        self.signals = MsixWorkerSignals()
        self._services = services
        self._confirmation_id = plan_confirmation_id
        self._plan = plan
        self._preview = preview

    @Slot()
    def run(self) -> None:
        """Emit a short-lived immediate gate or a sanitized failure."""
        try:
            result = self._services.service.prepare_runtime_confirmation(
                self._confirmation_id, self._plan, self._preview
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(result)


class MsixExecuteWorker(QRunnable):
    """Consume the immediate gate and observe one WinRT removal operation."""

    def __init__(
        self,
        services: MsixUninstallServices,
        runtime_confirmation_id: UUID,
        plan: MsixUninstallPlan,
        preview: MsixUninstallPreview,
    ) -> None:
        super().__init__()
        self.signals = MsixWorkerSignals()
        self.cancellation = CancellationToken()
        self._services = services
        self._confirmation_id = runtime_confirmation_id
        self._plan = plan
        self._preview = preview

    @Slot()
    def run(self) -> None:
        """Execute once and never retry an interrupted operation."""
        try:
            result = self._services.service.execute(
                self._confirmation_id,
                self._plan,
                self._preview,
                self.cancellation,
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(result)

    def cancel(self) -> None:
        """Cancel only before dispatch; never terminate Windows deployment work."""
        self.cancellation.cancel()


def require_prepared_msix(value: object) -> PreparedMsixWithServices:
    """Narrow a Qt payload to a prepared MSIX state."""
    if not isinstance(value, PreparedMsixWithServices):
        raise TypeError("worker emitted invalid MSIX preparation state")
    return value


def require_msix_runtime(value: object) -> PreparedMsixRuntimeConfirmation:
    """Narrow a Qt payload to an immediate confirmation state."""
    if not isinstance(value, PreparedMsixRuntimeConfirmation):
        raise TypeError("worker emitted invalid MSIX runtime confirmation")
    return value


def require_msix_result(value: object) -> MsixUninstallResult:
    """Narrow a Qt payload to a verified MSIX result."""
    if not isinstance(value, MsixUninstallResult):
        raise TypeError("worker emitted invalid MSIX uninstall result")
    return value
