"""Background workers for Stage 4C1 inventory, Preview, and execution."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from uuid import UUID

import pythoncom
from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.app.runtime import ApplicationRuntime, ServiceActionServices
from pc_manager_agent.confirmation.service_actions import ServiceActionConfirmation
from pc_manager_agent.domain.service_actions import (
    ServiceActionPlan,
    ServiceActionPreview,
    ServiceActionResult,
    ServiceActionType,
    ServiceInventoryItem,
)
from pc_manager_agent.orchestration.service_actions import ServiceActionService
from pc_manager_agent.safety.service_validator import ServiceSafetyReview
from pc_manager_agent.tools.manifest import CancellationToken


class ServiceWorkerSignals(QObject):
    """Thread-safe terminal signals shared by finite service workers."""

    completed = Signal(object)
    failed = Signal(str)


@dataclass(frozen=True)
class PreparedServiceAction:
    """Services plus one reviewed exact service Preview."""

    services: ServiceActionServices
    plan: ServiceActionPlan
    preview: ServiceActionPreview
    review: ServiceSafetyReview


@dataclass(frozen=True)
class RuntimeServicePreview:
    """Fresh runtime Preview and short-lived confirmation request."""

    preview: ServiceActionPreview
    confirmation: ServiceActionConfirmation


class ServiceInventoryWorker(QRunnable):
    """Read and classify services without blocking the GUI thread."""

    def __init__(self, runtime: ApplicationRuntime) -> None:
        super().__init__()
        self.signals = ServiceWorkerSignals()
        self._runtime = runtime

    @Slot()
    def run(self) -> None:
        """Return a fresh inventory or one friendly boundary error."""
        pythoncom.CoInitialize()
        try:
            value = self._runtime.create_service_action_services().service.list_current()
        except Exception as exc:
            _emit_failed(self.signals, exc)
        else:
            _emit_completed(self.signals, value)
        finally:
            pythoncom.CoUninitialize()


class ServicePrepareWorker(QRunnable):
    """Resolve one exact target and build a read-only Preview off-thread."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        user_goal: str,
        service_name: str,
        action: ServiceActionType,
    ) -> None:
        super().__init__()
        self.signals = ServiceWorkerSignals()
        self._runtime = runtime
        self._user_goal = user_goal
        self._service_name = service_name
        self._action = action

    @Slot()
    def run(self) -> None:
        """Prepare only the selected service name and requested action."""
        pythoncom.CoInitialize()
        try:
            services = self._runtime.create_service_action_services()
            plan, preview, review = services.service.prepare(
                self._user_goal,
                self._service_name,
                self._action,
            )
        except Exception as exc:
            _emit_failed(self.signals, exc)
        else:
            _emit_completed(
                self.signals,
                PreparedServiceAction(services, plan, preview, review),
            )
        finally:
            pythoncom.CoUninitialize()


class ServiceRuntimePreviewWorker(QRunnable):
    """Revalidate live service evidence immediately before approval."""

    def __init__(
        self,
        service: ServiceActionService,
        plan_confirmation_id: UUID,
        plan: ServiceActionPlan,
    ) -> None:
        super().__init__()
        self.signals = ServiceWorkerSignals()
        self._service = service
        self._plan_confirmation_id = plan_confirmation_id
        self._plan = plan

    @Slot()
    def run(self) -> None:
        """Return a fresh Preview and short-lived bound confirmation."""
        pythoncom.CoInitialize()
        try:
            preview, confirmation = self._service.request_runtime_confirmation(
                self._plan_confirmation_id,
                self._plan,
            )
        except Exception as exc:
            _emit_failed(self.signals, exc)
        else:
            _emit_completed(
                self.signals,
                RuntimeServicePreview(preview, confirmation),
            )
        finally:
            pythoncom.CoUninitialize()


class ServiceExecutionWorker(QRunnable):
    """Consume both approvals and execute the ordered action off-thread."""

    def __init__(
        self,
        service: ServiceActionService,
        plan_confirmation_id: UUID,
        runtime_confirmation_id: UUID,
        plan: ServiceActionPlan,
        preview: ServiceActionPreview,
    ) -> None:
        super().__init__()
        self.signals = ServiceWorkerSignals()
        self._service = service
        self._plan_confirmation_id = plan_confirmation_id
        self._runtime_confirmation_id = runtime_confirmation_id
        self._plan = plan
        self._preview = preview
        self._cancellation = CancellationToken()

    def cancel(self) -> None:
        """Prevent any future control step; current dispatched step remains bounded."""
        self._cancellation.cancel()

    @Slot()
    def run(self) -> None:
        """Execute and surface the verified final or partial service state."""
        pythoncom.CoInitialize()
        try:
            result = self._service.execute(
                self._plan_confirmation_id,
                self._runtime_confirmation_id,
                self._plan,
                self._preview,
                self._cancellation,
            )
        except Exception as exc:
            _emit_failed(self.signals, exc)
        else:
            _emit_completed(self.signals, result)
        finally:
            pythoncom.CoUninitialize()


def require_service_inventory(value: object) -> tuple[ServiceInventoryItem, ...]:
    """Narrow one Qt signal payload to a service inventory."""
    if not isinstance(value, tuple) or not all(
        isinstance(item, ServiceInventoryItem) for item in value
    ):
        raise TypeError("Worker emitted an invalid service inventory")
    return value


def require_prepared_service(value: object) -> PreparedServiceAction:
    """Narrow one Qt signal payload to a prepared service action."""
    if not isinstance(value, PreparedServiceAction):
        raise TypeError("Worker emitted an invalid service Preview")
    return value


def require_runtime_service(value: object) -> RuntimeServicePreview:
    """Narrow one Qt signal payload to a runtime service Preview."""
    if not isinstance(value, RuntimeServicePreview):
        raise TypeError("Worker emitted an invalid runtime service Preview")
    return value


def require_service_result(value: object) -> ServiceActionResult:
    """Narrow one Qt signal payload to a verified service action result."""
    if not isinstance(value, ServiceActionResult):
        raise TypeError("Worker emitted an invalid service action result")
    return value


def _emit_completed(signals: ServiceWorkerSignals, value: object) -> None:
    """Ignore only Qt deletion races after the owning dialog has closed."""
    with suppress(RuntimeError):
        signals.completed.emit(value)


def _emit_failed(signals: ServiceWorkerSignals, exc: Exception) -> None:
    """Ignore only Qt deletion races after the owning dialog has closed."""
    with suppress(RuntimeError):
        signals.failed.emit(f"{type(exc).__name__}: {exc}")
