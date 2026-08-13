"""Background workers for Stage 4B inventory, Preview, revalidation, and execution."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import pythoncom
from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.app.runtime import ApplicationRuntime, StartupActionServices
from pc_manager_agent.confirmation.startup_actions import StartupActionConfirmation
from pc_manager_agent.domain.startup_actions import (
    DisabledStartupRecord,
    StartupActionPlan,
    StartupActionPreview,
    StartupActionType,
    StartupIdentity,
    StartupMutationResult,
    StartupObservation,
    StartupSafetyAssessment,
)
from pc_manager_agent.orchestration.startup_actions import StartupActionService
from pc_manager_agent.safety.startup_validator import StartupSafetyReview


class StartupWorkerSignals(QObject):
    """Thread-safe terminal signals shared by finite startup workers."""

    completed = Signal(object)
    failed = Signal(str)


@dataclass(frozen=True)
class StartupInventory:
    """Fresh active observations and durable Agent-disabled records."""

    active: tuple[tuple[StartupObservation, StartupSafetyAssessment], ...]
    disabled: tuple[DisabledStartupRecord, ...]


@dataclass(frozen=True)
class PreparedStartupAction:
    """Services plus one backed-up, reviewed startup Preview."""

    services: StartupActionServices
    plan: StartupActionPlan
    preview: StartupActionPreview
    review: StartupSafetyReview


@dataclass(frozen=True)
class RuntimeStartupPreview:
    """Fresh runtime Preview and its short-lived confirmation request."""

    preview: StartupActionPreview
    confirmation: StartupActionConfirmation


class StartupInventoryWorker(QRunnable):
    """Read and classify startup entries without blocking the GUI thread."""

    def __init__(self, runtime: ApplicationRuntime) -> None:
        super().__init__()
        self.signals = StartupWorkerSignals()
        self._runtime = runtime

    @Slot()
    def run(self) -> None:
        """Return current and restorable inventory or one friendly boundary error."""
        pythoncom.CoInitialize()
        try:
            services = self._runtime.create_startup_action_services()
            value = StartupInventory(
                active=services.service.list_current(),
                disabled=services.service.list_disabled(),
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.signals.completed.emit(value)
        finally:
            pythoncom.CoUninitialize()


class StartupPrepareWorker(QRunnable):
    """Capture verified backup and build a read-only Preview off-thread."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        user_goal: str,
        action: StartupActionType,
        *,
        identity: StartupIdentity | None = None,
        backup_id: UUID | None = None,
    ) -> None:
        super().__init__()
        self.signals = StartupWorkerSignals()
        self._runtime = runtime
        self._user_goal = user_goal
        self._action = action
        self._identity = identity
        self._backup_id = backup_id

    @Slot()
    def run(self) -> None:
        """Prepare exactly the selected action without guessing another target."""
        pythoncom.CoInitialize()
        try:
            services = self._runtime.create_startup_action_services()
            if self._action is StartupActionType.DISABLE:
                if self._identity is None or self._backup_id is not None:
                    raise ValueError("Startup disable requires one selected active identity")
                plan, preview, review = services.service.prepare_disable(
                    self._user_goal,
                    self._identity,
                )
            else:
                if self._backup_id is None or self._identity is not None:
                    raise ValueError("Startup restore requires one Agent backup record")
                plan, preview, review = services.service.prepare_restore(
                    self._user_goal,
                    self._backup_id,
                )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.signals.completed.emit(PreparedStartupAction(services, plan, preview, review))
        finally:
            pythoncom.CoUninitialize()


class StartupRuntimePreviewWorker(QRunnable):
    """Revalidate live configuration and backup immediately before approval."""

    def __init__(
        self,
        service: StartupActionService,
        plan_confirmation_id: UUID,
        plan: StartupActionPlan,
    ) -> None:
        super().__init__()
        self.signals = StartupWorkerSignals()
        self._service = service
        self._plan_confirmation_id = plan_confirmation_id
        self._plan = plan

    @Slot()
    def run(self) -> None:
        """Return a fresh Preview plus a short-lived bound confirmation."""
        pythoncom.CoInitialize()
        try:
            preview, confirmation = self._service.request_runtime_confirmation(
                self._plan_confirmation_id,
                self._plan,
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.signals.completed.emit(RuntimeStartupPreview(preview, confirmation))
        finally:
            pythoncom.CoUninitialize()


class StartupExecutionWorker(QRunnable):
    """Consume both approvals and execute one narrow startup tool off-thread."""

    def __init__(
        self,
        service: StartupActionService,
        plan_confirmation_id: UUID,
        runtime_confirmation_id: UUID,
        plan: StartupActionPlan,
        preview: StartupActionPreview,
    ) -> None:
        super().__init__()
        self.signals = StartupWorkerSignals()
        self._service = service
        self._plan_confirmation_id = plan_confirmation_id
        self._runtime_confirmation_id = runtime_confirmation_id
        self._plan = plan
        self._preview = preview

    @Slot()
    def run(self) -> None:
        """Execute and surface the verified configuration result."""
        pythoncom.CoInitialize()
        try:
            result = self._service.execute(
                self._plan_confirmation_id,
                self._runtime_confirmation_id,
                self._plan,
                self._preview,
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.signals.completed.emit(result)
        finally:
            pythoncom.CoUninitialize()


def require_inventory(value: object) -> StartupInventory:
    """Narrow one Qt signal payload to a startup inventory."""
    if not isinstance(value, StartupInventory):
        raise TypeError("Worker emitted an invalid startup inventory")
    return value


def require_prepared_startup(value: object) -> PreparedStartupAction:
    """Narrow one Qt signal payload to a prepared startup action."""
    if not isinstance(value, PreparedStartupAction):
        raise TypeError("Worker emitted an invalid startup Preview")
    return value


def require_runtime_startup(value: object) -> RuntimeStartupPreview:
    """Narrow one Qt signal payload to a runtime startup Preview."""
    if not isinstance(value, RuntimeStartupPreview):
        raise TypeError("Worker emitted an invalid runtime startup Preview")
    return value


def require_startup_result(value: object) -> StartupMutationResult:
    """Narrow one Qt signal payload to a verified startup result."""
    if not isinstance(value, StartupMutationResult):
        raise TypeError("Worker emitted an invalid startup result")
    return value
