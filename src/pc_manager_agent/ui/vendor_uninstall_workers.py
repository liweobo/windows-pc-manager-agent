"""Qt workers for the finite Stage 4D2B Vendor uninstall workflow."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.app.runtime import ApplicationRuntime, VendorUninstallServices
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.domain.vendor_uninstall import (
    VendorUninstallExecutionReport,
    VendorUninstallPlan,
    VendorUninstallPreview,
)
from pc_manager_agent.orchestration.vendor_uninstall_execution import (
    PreparedVendorRuntimeConfirmation,
    PreparedVendorUninstall,
)
from pc_manager_agent.tools.manifest import CancellationToken


class VendorUninstallWorkerSignals(QObject):
    """Terminal signals shared by preparation, validation, and execution workers."""

    completed = Signal(object)
    failed = Signal(str)


@dataclass(frozen=True, slots=True)
class PreparedVendorUninstallWithServices:
    """Keep the fresh service graph with its prepared target result."""

    services: VendorUninstallServices
    prepared: PreparedVendorUninstall


class VendorUninstallPrepareWorker(QRunnable):
    """Refresh target, executable trust, policy, and preflight off the UI thread."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        user_goal: str,
        query: SoftwareTargetQuery,
    ) -> None:
        super().__init__()
        self.signals = VendorUninstallWorkerSignals()
        self.cancellation = CancellationToken()
        self._runtime = runtime
        self._user_goal = user_goal
        self._query = query

    @Slot()
    def run(self) -> None:
        """Return candidates or one plan-confirmation-bound Vendor Preview."""
        try:
            services = self._runtime.create_vendor_uninstall_services()
            prepared = services.service.prepare(
                self._user_goal,
                self._query,
                self.cancellation,
            )
        except Exception as exc:  # Qt boundary exposes one fail-closed message.
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(PreparedVendorUninstallWithServices(services, prepared))

    def cancel(self) -> None:
        """Cancel remaining read-only preparation work."""
        self.cancellation.cancel()


class VendorRuntimePrepareWorker(QRunnable):
    """Repeat every execution input immediately before the second confirmation."""

    def __init__(
        self,
        services: VendorUninstallServices,
        plan_confirmation_id: object,
        plan: VendorUninstallPlan,
    ) -> None:
        super().__init__()
        self.signals = VendorUninstallWorkerSignals()
        self.cancellation = CancellationToken()
        self._services = services
        self._plan_confirmation_id = plan_confirmation_id
        self._plan = plan

    @Slot()
    def run(self) -> None:
        """Issue a short-lived immediate confirmation from fresh local evidence."""
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
        except Exception as exc:  # Qt boundary exposes one fail-closed message.
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(prepared)

    def cancel(self) -> None:
        """Cancel runtime revalidation before any Vendor process is launched."""
        self.cancellation.cancel()


class VendorUninstallExecuteWorker(QRunnable):
    """Consume the immediate gate and monitor one interactive Vendor invocation."""

    def __init__(
        self,
        services: VendorUninstallServices,
        runtime_confirmation_id: object,
        plan: VendorUninstallPlan,
        preview: VendorUninstallPreview,
    ) -> None:
        super().__init__()
        self.signals = VendorUninstallWorkerSignals()
        self.cancellation = CancellationToken()
        self._services = services
        self._runtime_confirmation_id = runtime_confirmation_id
        self._plan = plan
        self._preview = preview

    @Slot()
    def run(self) -> None:
        """Run once; cancellation after launch stops monitoring but never kills a process."""
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
        except Exception as exc:  # Qt boundary exposes one fail-closed message.
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(report)

    def cancel(self) -> None:
        """Cancel pre-launch or stop monitoring without terminating Vendor processes."""
        self.cancellation.cancel()


def require_prepared_vendor_uninstall(value: object) -> PreparedVendorUninstallWithServices:
    """Narrow one Qt payload to a prepared Vendor state."""
    if not isinstance(value, PreparedVendorUninstallWithServices):
        raise TypeError("Worker emitted invalid Vendor preparation state")
    return value


def require_runtime_vendor_confirmation(value: object) -> PreparedVendorRuntimeConfirmation:
    """Narrow one Qt payload to a fresh Vendor runtime confirmation."""
    if not isinstance(value, PreparedVendorRuntimeConfirmation):
        raise TypeError("Worker emitted invalid Vendor runtime confirmation state")
    return value


def require_vendor_uninstall_report(value: object) -> VendorUninstallExecutionReport:
    """Narrow one Qt payload to the verified final Vendor report."""
    if not isinstance(value, VendorUninstallExecutionReport):
        raise TypeError("Worker emitted invalid Vendor uninstall report")
    return value
