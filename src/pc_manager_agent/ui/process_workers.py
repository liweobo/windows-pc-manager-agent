"""Background workers for Stage 4A Preview, revalidation, and execution."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.app.runtime import ApplicationRuntime, ProcessActionServices
from pc_manager_agent.confirmation.process_actions import ProcessActionConfirmation
from pc_manager_agent.domain.process_actions import (
    ProcessActionPlan,
    ProcessActionPreview,
    ProcessActionToolResult,
    ProcessActionType,
    ProcessTargetQuery,
)
from pc_manager_agent.orchestration.process_actions import ProcessActionService
from pc_manager_agent.safety.process_validator import ProcessSafetyReview
from pc_manager_agent.tools.manifest import CancellationToken


class ProcessWorkerSignals(QObject):
    """Thread-safe terminal signals shared by the finite process workers."""

    completed = Signal(object)
    failed = Signal(str)


@dataclass(frozen=True)
class PreparedProcessAction:
    """Services plus one freshly resolved and reviewed process Preview."""

    services: ProcessActionServices
    plan: ProcessActionPlan
    preview: ProcessActionPreview
    review: ProcessSafetyReview


@dataclass(frozen=True)
class RuntimeProcessPreview:
    """Revalidated Preview plus its short-lived runtime confirmation request."""

    preview: ProcessActionPreview
    confirmation: ProcessActionConfirmation


class ProcessPrepareWorker(QRunnable):
    """Resolve and classify a target without blocking the GUI thread."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        user_goal: str,
        *,
        query: ProcessTargetQuery | None = None,
        action: ProcessActionType | None = None,
    ) -> None:
        super().__init__()
        self.signals = ProcessWorkerSignals()
        self._runtime = runtime
        self._user_goal = user_goal
        self._query = query
        self._action = action

    @Slot()
    def run(self) -> None:
        """Build fresh services and a plan, reporting all boundary failures."""
        try:
            services = self._runtime.create_process_action_services()
            if self._query is None:
                plan, preview, review = services.service.prepare_from_text(self._user_goal)
            else:
                if self._action is None:
                    raise ValueError("An explicit process query requires an action")
                plan, preview, review = services.service.prepare(
                    self._user_goal,
                    self._query,
                    self._action,
                )
        except Exception as exc:  # Qt worker boundary reports typed text to the UI.
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(PreparedProcessAction(services, plan, preview, review))


class ProcessForcePreviewWorker(QRunnable):
    """Create a new force transaction after a graceful result or unsupported Preview."""

    def __init__(self, service: ProcessActionService, graceful_plan: ProcessActionPlan) -> None:
        super().__init__()
        self.signals = ProcessWorkerSignals()
        self._service = service
        self._graceful_plan = graceful_plan

    @Slot()
    def run(self) -> None:
        """Resolve a completely new high-impact plan; never reuse approval."""
        try:
            plan, preview, review = self._service.prepare_force_after_graceful(self._graceful_plan)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit((plan, preview, review))


class ProcessRuntimePreviewWorker(QRunnable):
    """Revalidate identity and policy immediately before runtime confirmation."""

    def __init__(
        self,
        service: ProcessActionService,
        plan_confirmation_id: UUID,
        plan: ProcessActionPlan,
    ) -> None:
        super().__init__()
        self.signals = ProcessWorkerSignals()
        self._service = service
        self._plan_confirmation_id = plan_confirmation_id
        self._plan = plan

    @Slot()
    def run(self) -> None:
        """Return only a fresh Preview and short-lived bound confirmation."""
        try:
            preview, confirmation = self._service.request_runtime_confirmation(
                self._plan_confirmation_id,
                self._plan,
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(RuntimeProcessPreview(preview, confirmation))


class ProcessExecutionWorker(QRunnable):
    """Consume both confirmations and invoke the registered narrow tool off-thread."""

    def __init__(
        self,
        service: ProcessActionService,
        plan_confirmation_id: UUID,
        runtime_confirmation_id: UUID,
        plan: ProcessActionPlan,
        preview: ProcessActionPreview,
    ) -> None:
        super().__init__()
        self.signals = ProcessWorkerSignals()
        self.cancellation = CancellationToken()
        self._service = service
        self._plan_confirmation_id = plan_confirmation_id
        self._runtime_confirmation_id = runtime_confirmation_id
        self._plan = plan
        self._preview = preview

    @Slot()
    def run(self) -> None:
        """Execute and surface the exact verified member results."""
        try:
            result = self._service.execute(
                self._plan_confirmation_id,
                self._runtime_confirmation_id,
                self._plan,
                self._preview,
                self.cancellation,
            )
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.completed.emit(result)

    def cancel(self) -> None:
        """Cancel only future requests and graceful waiting; do not pretend to Undo."""
        self.cancellation.cancel()


def require_prepared_process_action(value: object) -> PreparedProcessAction:
    """Narrow a Qt signal payload to one prepared action."""
    if not isinstance(value, PreparedProcessAction):
        raise TypeError("Worker emitted an invalid process Preview")
    return value


def require_runtime_process_preview(value: object) -> RuntimeProcessPreview:
    """Narrow a Qt signal payload to the fresh runtime Preview."""
    if not isinstance(value, RuntimeProcessPreview):
        raise TypeError("Worker emitted an invalid runtime process Preview")
    return value


def require_process_result(value: object) -> ProcessActionToolResult:
    """Narrow a Qt signal payload to a verified process tool result."""
    if not isinstance(value, ProcessActionToolResult):
        raise TypeError("Worker emitted an invalid process action result")
    return value
