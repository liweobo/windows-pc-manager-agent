"""Qt workers for the finite Stage 4D2C1 winget uninstall workflow."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.app.runtime import ApplicationRuntime, WingetUninstallServices
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.domain.winget_uninstall import (
    WingetUninstallExecutionReport,
    WingetUninstallPlan,
    WingetUninstallPreview,
)
from pc_manager_agent.orchestration.winget_uninstall_execution import (
    PreparedWingetRuntimeConfirmation,
    PreparedWingetUninstall,
)
from pc_manager_agent.tools.manifest import CancellationToken


class WingetWorkerSignals(QObject):
    """Terminal signals shared by preparation, validation, and execution workers."""

    completed = Signal(object)
    failed = Signal(str)


@dataclass(frozen=True, slots=True)
class PreparedWingetUninstallWithServices:
    """Keep the fresh dependency graph with its prepared result."""

    services: WingetUninstallServices
    prepared: PreparedWingetUninstall


class WingetUninstallPrepareWorker(QRunnable):
    """Refresh package, software, mapping, executable, policy, and preflight off the UI thread."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        user_goal: str,
        query: SoftwareTargetQuery,
    ) -> None:
        super().__init__()
        self.signals = WingetWorkerSignals()
        self.cancellation = CancellationToken()
        self._runtime = runtime
        self._user_goal = user_goal
        self._query = query

    @Slot()
    def run(self) -> None:
        """Return one plan-confirmation-bound Preview or a fail-closed result."""
        try:
            services = self._runtime.create_winget_uninstall_services()
            prepared = services.service.prepare(
                self._user_goal,
                self._query,
                self.cancellation,
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(PreparedWingetUninstallWithServices(services, prepared))

    def cancel(self) -> None:
        """Cancel remaining read-only preparation work."""
        self.cancellation.cancel()


class WingetRuntimePrepareWorker(QRunnable):
    """Repeat every execution input immediately before the second confirmation."""

    def __init__(
        self,
        services: WingetUninstallServices,
        plan_confirmation_id: UUID,
        plan: WingetUninstallPlan,
    ) -> None:
        super().__init__()
        self.signals = WingetWorkerSignals()
        self.cancellation = CancellationToken()
        self._services = services
        self._plan_confirmation_id = plan_confirmation_id
        self._plan = plan

    @Slot()
    def run(self) -> None:
        """Emit a fresh Preview and short-lived immediate gate."""
        try:
            prepared = self._services.service.prepare_runtime_confirmation(
                self._plan_confirmation_id,
                self._plan,
                self.cancellation,
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(prepared)

    def cancel(self) -> None:
        """Cancel revalidation before any package-manager process is launched."""
        self.cancellation.cancel()


class WingetUninstallExecuteWorker(QRunnable):
    """Consume the immediate gate and monitor one fixed winget invocation."""

    def __init__(
        self,
        services: WingetUninstallServices,
        runtime_confirmation_id: UUID,
        plan: WingetUninstallPlan,
        preview: WingetUninstallPreview,
    ) -> None:
        super().__init__()
        self.signals = WingetWorkerSignals()
        self.cancellation = CancellationToken()
        self._services = services
        self._runtime_confirmation_id = runtime_confirmation_id
        self._plan = plan
        self._preview = preview

    @Slot()
    def run(self) -> None:
        """Execute once; cancellation after launch stops monitoring without killing."""
        try:
            report = self._services.service.execute(
                self._runtime_confirmation_id,
                self._plan,
                self._preview,
                self.cancellation,
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(report)

    def cancel(self) -> None:
        """Cancel before launch or stop monitoring after launch without termination."""
        self.cancellation.cancel()


def require_prepared_winget_uninstall(value: object) -> PreparedWingetUninstallWithServices:
    """Narrow one Qt payload to a prepared winget state."""
    if not isinstance(value, PreparedWingetUninstallWithServices):
        raise TypeError("worker emitted invalid winget preparation state")
    return value


def require_runtime_winget_confirmation(value: object) -> PreparedWingetRuntimeConfirmation:
    """Narrow one Qt payload to a fresh immediate confirmation."""
    if not isinstance(value, PreparedWingetRuntimeConfirmation):
        raise TypeError("worker emitted invalid winget runtime confirmation state")
    return value


def require_winget_uninstall_report(value: object) -> WingetUninstallExecutionReport:
    """Narrow one Qt payload to a dual-verified final report."""
    if not isinstance(value, WingetUninstallExecutionReport):
        raise TypeError("worker emitted invalid winget uninstall report")
    return value
