"""Qt workers for the finite Stage 4D2A MSI uninstall workflow."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.app.runtime import ApplicationRuntime, MsiUninstallServices
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiUninstallExecutionReport,
    MsiUninstallPlan,
    MsiUninstallPreview,
)
from pc_manager_agent.orchestration.software_uninstall_execution import (
    PreparedMsiRuntimeConfirmation,
    PreparedMsiUninstall,
)
from pc_manager_agent.tools.manifest import CancellationToken


class MsiUninstallWorkerSignals(QObject):
    """Terminal signals shared by uninstall preparation and execution workers."""

    completed = Signal(object)
    failed = Signal(str)


@dataclass(frozen=True, slots=True)
class PreparedMsiUninstallWithServices:
    """Keep the fresh service graph with its prepared target result."""

    services: MsiUninstallServices
    prepared: PreparedMsiUninstall


class MsiUninstallPrepareWorker(QRunnable):
    """Refresh identity, MSI registration, policy, and preflight off the UI thread."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        user_goal: str,
        query: SoftwareTargetQuery,
    ) -> None:
        super().__init__()
        self.signals = MsiUninstallWorkerSignals()
        self.cancellation = CancellationToken()
        self._runtime = runtime
        self._user_goal = user_goal
        self._query = query

    @Slot()
    def run(self) -> None:
        """Return candidates or one plan-confirmation-bound Preview."""
        try:
            services = self._runtime.create_msi_uninstall_services()
            prepared = services.service.prepare(
                self._user_goal,
                self._query,
                self.cancellation,
            )
        except Exception as exc:  # Qt boundary turns a fail-closed error into visible text.
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(PreparedMsiUninstallWithServices(services, prepared))

    def cancel(self) -> None:
        """Cancel remaining read-only preparation work."""
        self.cancellation.cancel()


class MsiRuntimePrepareWorker(QRunnable):
    """Repeat all execution evidence immediately before the second confirmation."""

    def __init__(
        self,
        services: MsiUninstallServices,
        plan_confirmation_id: object,
        plan: MsiUninstallPlan,
    ) -> None:
        super().__init__()
        self.signals = MsiUninstallWorkerSignals()
        self.cancellation = CancellationToken()
        self._services = services
        self._plan_confirmation_id = plan_confirmation_id
        self._plan = plan

    @Slot()
    def run(self) -> None:
        """Issue the short-lived immediate confirmation from fresh local evidence."""
        from uuid import UUID

        if not isinstance(self._plan_confirmation_id, UUID):
            self.signals.failed.emit("TypeError: invalid plan confirmation identifier")
            return
        try:
            prepared = self._services.service.prepare_runtime_confirmation(
                self._plan_confirmation_id,
                self._plan,
                self.cancellation,
            )
        except Exception as exc:  # Qt boundary turns a fail-closed error into visible text.
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(prepared)

    def cancel(self) -> None:
        """Cancel runtime revalidation before an installer is launched."""
        self.cancellation.cancel()


class MsiUninstallExecuteWorker(QRunnable):
    """Consume the immediate confirmation and monitor one fixed MSI invocation."""

    def __init__(
        self,
        services: MsiUninstallServices,
        runtime_confirmation_id: object,
        plan: MsiUninstallPlan,
        preview: MsiUninstallPreview,
    ) -> None:
        super().__init__()
        self.signals = MsiUninstallWorkerSignals()
        self.cancellation = CancellationToken()
        self._services = services
        self._runtime_confirmation_id = runtime_confirmation_id
        self._plan = plan
        self._preview = preview

    @Slot()
    def run(self) -> None:
        """Run once; cancellation after launch is recorded but never kills Windows Installer."""
        from uuid import UUID

        if not isinstance(self._runtime_confirmation_id, UUID):
            self.signals.failed.emit("TypeError: invalid runtime confirmation identifier")
            return
        try:
            report = self._services.service.execute(
                self._runtime_confirmation_id,
                self._plan,
                self._preview,
                self.cancellation,
            )
        except Exception as exc:  # Qt boundary turns a fail-closed error into visible text.
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(report)

    def cancel(self) -> None:
        """Cancel before launch or record a request after launch without terminating MSI."""
        self.cancellation.cancel()


def require_prepared_msi_uninstall(value: object) -> PreparedMsiUninstallWithServices:
    """Narrow one Qt payload to a prepared uninstall state."""
    if not isinstance(value, PreparedMsiUninstallWithServices):
        raise TypeError("Worker emitted invalid MSI preparation state")
    return value


def require_runtime_msi_confirmation(value: object) -> PreparedMsiRuntimeConfirmation:
    """Narrow one Qt payload to a fresh runtime Preview and confirmation."""
    if not isinstance(value, PreparedMsiRuntimeConfirmation):
        raise TypeError("Worker emitted invalid MSI runtime confirmation state")
    return value


def require_msi_uninstall_report(value: object) -> MsiUninstallExecutionReport:
    """Narrow one Qt payload to the verified final report."""
    if not isinstance(value, MsiUninstallExecutionReport):
        raise TypeError("Worker emitted invalid MSI uninstall report")
    return value
