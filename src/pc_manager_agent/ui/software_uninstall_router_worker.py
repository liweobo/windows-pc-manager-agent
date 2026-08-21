"""Qt worker for read-only MSI/Vendor uninstall mechanism routing."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.orchestration.software_uninstall_router import SoftwareUninstallRoute
from pc_manager_agent.tools.manifest import CancellationToken


class SoftwareUninstallRouteSignals(QObject):
    """Terminal signals for one read-only route decision."""

    completed = Signal(object)
    failed = Signal(str)


class SoftwareUninstallRouteWorker(QRunnable):
    """Resolve one target and mechanism without blocking the Qt event loop."""

    def __init__(self, runtime: ApplicationRuntime, query: SoftwareTargetQuery) -> None:
        super().__init__()
        self.signals = SoftwareUninstallRouteSignals()
        self.cancellation = CancellationToken()
        self._runtime = runtime
        self._query = query

    @Slot()
    def run(self) -> None:
        """Emit exactly one safe route result or sanitized failure text."""
        try:
            route = self._runtime.create_software_uninstall_router().route(
                self._query,
                self.cancellation,
            )
        except Exception as exc:  # Qt boundary exposes one fail-closed message.
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(route)

    def cancel(self) -> None:
        """Cancel remaining read-only inventory work."""
        self.cancellation.cancel()


def require_software_uninstall_route(value: object) -> SoftwareUninstallRoute:
    """Narrow one Qt payload to a deterministic route result."""
    if not isinstance(value, SoftwareUninstallRoute):
        raise TypeError("Worker emitted an invalid software uninstall route")
    return value
