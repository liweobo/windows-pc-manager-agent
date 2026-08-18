"""Background workers for Stage 4C2 Preview, confirmation, execution, and history."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from uuid import UUID

import pythoncom
from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.app.runtime import ApplicationRuntime, ServiceStartupActionServices
from pc_manager_agent.confirmation.service_startup_actions import (
    ServiceStartupActionConfirmation,
)
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionPlan,
    ServiceStartupActionPreview,
    ServiceStartupActionType,
    ServiceStartupChangeRecord,
    ServiceStartupMutationResult,
)
from pc_manager_agent.orchestration.service_startup_actions import ServiceStartupActionService
from pc_manager_agent.safety.service_startup_validator import ServiceStartupSafetyReview
from pc_manager_agent.tools.manifest import CancellationToken


class ServiceStartupWorkerSignals(QObject):
    """Thread-safe terminal signals shared by finite Stage 4C2 workers."""

    completed = Signal(object)
    failed = Signal(str)


@dataclass(frozen=True)
class PreparedServiceStartupAction:
    """Services plus one reviewed, backed-up configuration Preview."""

    services: ServiceStartupActionServices
    plan: ServiceStartupActionPlan
    preview: ServiceStartupActionPreview
    review: ServiceStartupSafetyReview


@dataclass(frozen=True)
class RuntimeServiceStartupPreview:
    """Fresh runtime Preview and its short-lived confirmation request."""

    preview: ServiceStartupActionPreview
    confirmation: ServiceStartupActionConfirmation


class ServiceStartupPrepareWorker(QRunnable):
    """Resolve, back up, and Preview one exact service configuration off-thread."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        user_goal: str,
        action: ServiceStartupActionType,
        *,
        identity: object | None = None,
        restore_backup_id: UUID | None = None,
    ) -> None:
        super().__init__()
        self.signals = ServiceStartupWorkerSignals()
        self._runtime = runtime
        self._user_goal = user_goal
        self._action = action
        self._identity = identity
        self._restore_backup_id = restore_backup_id

    @Slot()
    def run(self) -> None:
        """Prepare either one exact change or one conflict-checked restore."""
        pythoncom.CoInitialize()
        try:
            services = self._runtime.create_service_startup_action_services()
            if self._action is ServiceStartupActionType.RESTORE:
                if self._restore_backup_id is None:
                    raise ValueError("Restore requires one Agent backup ID")
                plan, preview, review = services.service.prepare_restore(
                    self._user_goal,
                    self._restore_backup_id,
                )
            else:
                if self._identity is None:
                    raise ValueError("Service startup change requires exact local identity")
                plan, preview, review = services.service.prepare_change(
                    self._user_goal,
                    self._identity,
                    self._action,
                )
        except Exception as exc:
            _emit_failed(self.signals, exc)
        else:
            _emit_completed(
                self.signals,
                PreparedServiceStartupAction(services, plan, preview, review),
            )
        finally:
            pythoncom.CoUninitialize()


class ServiceStartupRuntimePreviewWorker(QRunnable):
    """Revalidate configuration, impact, permission, and backup before immediate approval."""

    def __init__(
        self,
        service: ServiceStartupActionService,
        plan_confirmation_id: UUID,
        plan: ServiceStartupActionPlan,
    ) -> None:
        super().__init__()
        self.signals = ServiceStartupWorkerSignals()
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
                RuntimeServiceStartupPreview(preview, confirmation),
            )
        finally:
            pythoncom.CoUninitialize()


class ServiceStartupExecutionWorker(QRunnable):
    """Consume both approvals and execute one narrow configuration tool off-thread."""

    def __init__(
        self,
        service: ServiceStartupActionService,
        plan_confirmation_id: UUID,
        runtime_confirmation_id: UUID,
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
    ) -> None:
        super().__init__()
        self.signals = ServiceStartupWorkerSignals()
        self._service = service
        self._plan_confirmation_id = plan_confirmation_id
        self._runtime_confirmation_id = runtime_confirmation_id
        self._plan = plan
        self._preview = preview
        self._cancellation = CancellationToken()

    def cancel(self) -> None:
        """Prevent dispatch if cancellation arrives before ChangeServiceConfig."""
        self._cancellation.cancel()

    @Slot()
    def run(self) -> None:
        """Execute and surface only the read-back verified result."""
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


class ServiceStartupHistoryWorker(QRunnable):
    """Load bounded restorable history without blocking the GUI thread."""

    def __init__(self, runtime: ApplicationRuntime) -> None:
        super().__init__()
        self.signals = ServiceStartupWorkerSignals()
        self._runtime = runtime

    @Slot()
    def run(self) -> None:
        """Return Agent-owned unrestored changes or a friendly storage error."""
        pythoncom.CoInitialize()
        try:
            changes = self._runtime.create_service_startup_action_services().service.list_changes()
        except Exception as exc:
            _emit_failed(self.signals, exc)
        else:
            _emit_completed(self.signals, changes)
        finally:
            pythoncom.CoUninitialize()


def require_prepared_service_startup(value: object) -> PreparedServiceStartupAction:
    """Narrow one Qt signal payload to a prepared Stage 4C2 action."""
    if not isinstance(value, PreparedServiceStartupAction):
        raise TypeError("Worker emitted an invalid service startup Preview")
    return value


def require_runtime_service_startup(value: object) -> RuntimeServiceStartupPreview:
    """Narrow one Qt signal payload to a runtime Stage 4C2 Preview."""
    if not isinstance(value, RuntimeServiceStartupPreview):
        raise TypeError("Worker emitted an invalid runtime service startup Preview")
    return value


def require_service_startup_result(value: object) -> ServiceStartupMutationResult:
    """Narrow one Qt signal payload to a verified configuration result."""
    if not isinstance(value, ServiceStartupMutationResult):
        raise TypeError("Worker emitted an invalid service startup result")
    return value


def require_service_startup_history(value: object) -> tuple[ServiceStartupChangeRecord, ...]:
    """Narrow one Qt signal payload to bounded restore history."""
    if not isinstance(value, tuple) or not all(
        isinstance(item, ServiceStartupChangeRecord) for item in value
    ):
        raise TypeError("Worker emitted invalid service startup history")
    return value


def _emit_completed(signals: ServiceStartupWorkerSignals, value: object) -> None:
    with suppress(RuntimeError):
        signals.completed.emit(value)


def _emit_failed(signals: ServiceStartupWorkerSignals, exc: Exception) -> None:
    with suppress(RuntimeError):
        signals.failed.emit(f"{type(exc).__name__}: {exc}")
