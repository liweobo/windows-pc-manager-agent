"""Finite background workers for Stage 4X2 preparation, UAC, IPC, and readback."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass

import pythoncom
from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.orchestration.elevated_service_actions import ElevatedDispatchOutcome
from pc_manager_agent.orchestration.elevated_service_preparation import (
    ElevatedServicePreparationService,
    PreparedElevatedServiceAction,
)
from pc_manager_agent.orchestration.privileged_actions import (
    PreparedPrivilegedRuntimeConfirmation,
)
from pc_manager_agent.ui.service_workers import PreparedServiceAction


class ElevatedServiceWorkerSignals(QObject):
    """Thread-safe terminal signals for finite Stage 4X2 workers."""

    completed = Signal(object)
    failed = Signal(str)


@dataclass(frozen=True, slots=True)
class PreparedElevatedServiceUi:
    """Prepared privileged plan together with its owning orchestration service."""

    service: ElevatedServicePreparationService
    prepared: PreparedElevatedServiceAction


@dataclass(frozen=True, slots=True)
class RuntimeElevatedServiceUi:
    """Fresh short-lived privileged Preview ready for immediate confirmation."""

    service: ElevatedServicePreparationService
    prepared: PreparedElevatedServiceAction
    runtime: PreparedPrivilegedRuntimeConfirmation


class ElevatedServicePrepareWorker(QRunnable):
    """Compose and prepare the separate Stage 4X2 plan without blocking Qt."""

    def __init__(self, runtime: ApplicationRuntime, source: PreparedServiceAction) -> None:
        super().__init__()
        self.signals = ElevatedServiceWorkerSignals()
        self._runtime = runtime
        self._source = source

    @Slot()
    def run(self) -> None:
        """Build a real-mode Preview only for a Stage 4C1 permission-only block."""
        pythoncom.CoInitialize()
        try:
            service = self._runtime.create_elevated_service_preparation_service(
                self._source.services
            )
            prepared = service.prepare(self._source.plan, self._source.preview)
        except Exception as exc:
            _failed(self.signals, exc)
        else:
            _completed(self.signals, PreparedElevatedServiceUi(service, prepared))
        finally:
            pythoncom.CoUninitialize()


class ElevatedServiceRuntimeWorker(QRunnable):
    """Repeat service safety/state/dependency checks before immediate confirmation."""

    def __init__(self, value: PreparedElevatedServiceUi) -> None:
        super().__init__()
        self.signals = ElevatedServiceWorkerSignals()
        self._value = value

    @Slot()
    def run(self) -> None:
        """Create one fresh runtime Preview after the privileged plan was approved."""
        pythoncom.CoInitialize()
        try:
            runtime = self._value.service.prepare_runtime(self._value.prepared)
        except Exception as exc:
            _failed(self.signals, exc)
        else:
            _completed(
                self.signals,
                RuntimeElevatedServiceUi(
                    self._value.service,
                    self._value.prepared,
                    runtime,
                ),
            )
        finally:
            pythoncom.CoUninitialize()


class ElevatedServiceDispatchWorker(QRunnable):
    """Consume immediate approval, request UAC once, exchange once, and verify once."""

    def __init__(self, value: RuntimeElevatedServiceUi) -> None:
        super().__init__()
        self.signals = ElevatedServiceWorkerSignals()
        self._value = value

    @Slot()
    def run(self) -> None:
        """Dispatch the exact capability; cancellation or interruption never retries."""
        pythoncom.CoInitialize()
        try:
            envelope = self._value.service.approve_runtime_and_build(
                self._value.prepared,
                self._value.runtime,
                True,
            )
            outcome = self._value.service.dispatch(envelope)
        except Exception as exc:
            _failed(self.signals, exc)
        else:
            _completed(self.signals, outcome)
        finally:
            pythoncom.CoUninitialize()


def require_prepared_elevated(value: object) -> PreparedElevatedServiceUi:
    """Narrow one Qt signal payload to a prepared Stage 4X2 action."""
    if not isinstance(value, PreparedElevatedServiceUi):
        raise TypeError("Worker emitted an invalid Stage 4X2 plan")
    return value


def require_runtime_elevated(value: object) -> RuntimeElevatedServiceUi:
    """Narrow one Qt signal payload to a runtime Stage 4X2 Preview."""
    if not isinstance(value, RuntimeElevatedServiceUi):
        raise TypeError("Worker emitted an invalid Stage 4X2 runtime Preview")
    return value


def require_elevated_outcome(value: object) -> ElevatedDispatchOutcome:
    """Narrow one Qt signal payload to an authenticated/read-back outcome."""
    if not isinstance(value, ElevatedDispatchOutcome):
        raise TypeError("Worker emitted an invalid Stage 4X2 result")
    return value


def _completed(signals: ElevatedServiceWorkerSignals, value: object) -> None:
    with suppress(RuntimeError):
        signals.completed.emit(value)


def _failed(signals: ElevatedServiceWorkerSignals, exc: Exception) -> None:
    with suppress(RuntimeError):
        signals.failed.emit(f"{type(exc).__name__}: {exc}")
