"""Finite Qt workers for dedicated Stage 4X3 privileged actions."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

import pythoncom
from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.service_actions import ServiceStableIdentity
from pc_manager_agent.domain.service_startup_actions import ServiceStartupActionType
from pc_manager_agent.domain.startup_actions import StartupIdentity
from pc_manager_agent.orchestration.elevated_service_actions import ElevatedDispatchOutcome
from pc_manager_agent.orchestration.elevated_stage4x3 import (
    ElevatedStage4X3PreparationService,
    PreparedStage4X3Action,
)
from pc_manager_agent.orchestration.privileged_actions import (
    PreparedPrivilegedRuntimeConfirmation,
)


class Stage4X3UiAction(StrEnum):
    """Finite UI intents; no generic privileged-operation choice exists."""

    SERVICE_STARTUP_CHANGE = "SERVICE_STARTUP_CHANGE"
    SERVICE_STARTUP_RESTORE = "SERVICE_STARTUP_RESTORE"
    STARTUP_MACHINE_DISABLE = "STARTUP_MACHINE_DISABLE"
    STARTUP_MACHINE_RESTORE = "STARTUP_MACHINE_RESTORE"
    MSI_UNINSTALL_MACHINE = "MSI_UNINSTALL_MACHINE"


@dataclass(frozen=True, slots=True)
class Stage4X3UiRequest:
    """Typed local UI request resolved again by deterministic orchestration."""

    action: Stage4X3UiAction
    user_goal: str
    display_name: str
    service_identity: ServiceStableIdentity | None = None
    service_action: ServiceStartupActionType | None = None
    startup_identity: StartupIdentity | None = None
    backup_id: UUID | None = None
    software_identity_digest: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedStage4X3Ui:
    """Prepared action together with the orchestration service that owns approvals."""

    service: ElevatedStage4X3PreparationService
    request: Stage4X3UiRequest
    prepared: PreparedStage4X3Action


@dataclass(frozen=True, slots=True)
class RuntimeStage4X3Ui:
    """Fresh short-lived Preview awaiting its independent immediate confirmation."""

    prepared_ui: PreparedStage4X3Ui
    runtime: PreparedPrivilegedRuntimeConfirmation


class Stage4X3WorkerSignals(QObject):
    """Thread-safe terminal signals for finite Stage 4X3 background work."""

    completed = Signal(object)
    failed = Signal(str)


class Stage4X3PrepareWorker(QRunnable):
    """Prepare one typed R3 action without blocking Qt or displaying UAC."""

    def __init__(self, runtime: ApplicationRuntime, request: Stage4X3UiRequest) -> None:
        super().__init__()
        self.signals = Stage4X3WorkerSignals()
        self._runtime = runtime
        self._request = request

    @Slot()
    def run(self) -> None:
        """Resolve exact identity, policy, backup, and Preview for one UI intent."""
        pythoncom.CoInitialize()
        try:
            service = self._runtime.create_stage4x3_action_services().preparation
            prepared = _prepare(service, self._request)
        except Exception as exc:
            _failed(self.signals, exc)
        else:
            _completed(self.signals, PreparedStage4X3Ui(service, self._request, prepared))
        finally:
            pythoncom.CoUninitialize()


class Stage4X3RuntimeWorker(QRunnable):
    """Repeat Main-side safety and identity evidence after plan approval."""

    def __init__(self, value: PreparedStage4X3Ui) -> None:
        super().__init__()
        self.signals = Stage4X3WorkerSignals()
        self._value = value

    @Slot()
    def run(self) -> None:
        """Build a fresh runtime Preview without launching the Broker."""
        pythoncom.CoInitialize()
        try:
            runtime = self._value.service.prepare_runtime(self._value.prepared)
        except Exception as exc:
            _failed(self.signals, exc)
        else:
            _completed(self.signals, RuntimeStage4X3Ui(self._value, runtime))
        finally:
            pythoncom.CoUninitialize()


class Stage4X3DispatchWorker(QRunnable):
    """Consume immediate approval, request UAC once, and perform Main readback once."""

    def __init__(self, value: RuntimeStage4X3Ui) -> None:
        super().__init__()
        self.signals = Stage4X3WorkerSignals()
        self._value = value

    @Slot()
    def run(self) -> None:
        """Dispatch the exact signed capability without retry or mechanism fallback."""
        pythoncom.CoInitialize()
        try:
            source = self._value.prepared_ui
            envelope = source.service.approve_runtime_and_build(
                source.prepared,
                self._value.runtime,
                True,
            )
            outcome = source.service.dispatch(envelope)
        except Exception as exc:
            _failed(self.signals, exc)
        else:
            _completed(self.signals, outcome)
        finally:
            pythoncom.CoUninitialize()


def require_prepared_stage4x3(value: object) -> PreparedStage4X3Ui:
    """Narrow one Qt payload to a prepared Stage 4X3 action."""
    if not isinstance(value, PreparedStage4X3Ui):
        raise TypeError("Worker emitted an invalid Stage 4X3 plan")
    return value


def require_runtime_stage4x3(value: object) -> RuntimeStage4X3Ui:
    """Narrow one Qt payload to a fresh Stage 4X3 runtime Preview."""
    if not isinstance(value, RuntimeStage4X3Ui):
        raise TypeError("Worker emitted an invalid Stage 4X3 runtime Preview")
    return value


def require_stage4x3_outcome(value: object) -> ElevatedDispatchOutcome:
    """Narrow one Qt payload to an authenticated Broker/readback result."""
    if not isinstance(value, ElevatedDispatchOutcome):
        raise TypeError("Worker emitted an invalid Stage 4X3 result")
    return value


def _prepare(
    service: ElevatedStage4X3PreparationService,
    request: Stage4X3UiRequest,
) -> PreparedStage4X3Action:
    if request.action is Stage4X3UiAction.SERVICE_STARTUP_CHANGE:
        if request.service_identity is None or request.service_action is None:
            raise ValueError("Service startup change requires exact identity and action")
        return service.prepare_service_startup_change(
            request.user_goal,
            request.service_identity,
            request.service_action,
        )
    if request.action is Stage4X3UiAction.SERVICE_STARTUP_RESTORE:
        if request.backup_id is None:
            raise ValueError("Service startup restore requires one Agent backup")
        return service.prepare_service_startup_restore(request.user_goal, request.backup_id)
    if request.action is Stage4X3UiAction.STARTUP_MACHINE_DISABLE:
        if request.startup_identity is None:
            raise ValueError("Machine startup disable requires one exact HKLM identity")
        return service.prepare_machine_startup_disable(
            request.user_goal,
            request.startup_identity,
        )
    if request.action is Stage4X3UiAction.STARTUP_MACHINE_RESTORE:
        if request.backup_id is None:
            raise ValueError("Machine startup restore requires one Agent backup")
        return service.prepare_machine_startup_restore(request.user_goal, request.backup_id)
    if request.action is Stage4X3UiAction.MSI_UNINSTALL_MACHINE:
        if request.software_identity_digest is None:
            raise ValueError("Machine MSI uninstall requires one exact identity digest")
        return service.prepare_machine_msi_uninstall(
            request.user_goal,
            request.software_identity_digest,
        )
    raise ValueError("Unsupported Stage 4X3 UI action")


def _completed(signals: Stage4X3WorkerSignals, value: object) -> None:
    with suppress(RuntimeError):
        signals.completed.emit(value)


def _failed(signals: Stage4X3WorkerSignals, exc: Exception) -> None:
    with suppress(RuntimeError):
        signals.failed.emit(f"{type(exc).__name__}: {exc}")
