"""One cancellable Office operation per UI worker; raw parser/provider errors are never shown."""

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.domain.office_documents import OfficeError
from pc_manager_agent.tools.manifest import CancellationToken


class OfficeWorkerSignals(QObject):
    """Queued Qt handoff of typed domain results and stable user-safe failure codes."""

    completed = Signal(object)
    failed = Signal(str)


class OfficeWorker(QRunnable):
    """Run only an injected domain operation, never a string command or model-generated code."""

    def __init__(self, task: Callable[[CancellationToken], object]) -> None:
        super().__init__()
        self.signals = OfficeWorkerSignals()
        self.cancellation = CancellationToken()
        self._task = task

    @Slot()
    def run(self) -> None:
        """Convert all worker-boundary failures to stable codes without content/credentials."""
        try:
            result = self._task(self.cancellation)
        except Exception as exc:
            self.signals.failed.emit(
                exc.code if isinstance(exc, OfficeError) else "OFFICE_OPERATION_FAILED"
            )
        else:
            self.signals.completed.emit(result)

    def cancel(self) -> None:
        """Cancel future work cooperatively; never terminate Word/Excel or unlock a file."""
        self.cancellation.cancel()
