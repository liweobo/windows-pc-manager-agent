"""Cancellable, off-GUI source/provenance preparation for one review item."""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from pc_manager_agent.orchestration.optimization_session import OptimizationSessionService


class OptimizationReviewSignals(QObject):
    """Communicate validated preparation or sanitized failure to the UI thread."""

    completed = Signal(object)
    failed = Signal(str)


class OptimizationReviewWorker(QRunnable):
    """Only prepare a review; cancellation is owned by the shared session service."""

    def __init__(
        self, sessions: OptimizationSessionService, session_id: UUID, recommendation_id: UUID
    ) -> None:
        super().__init__()
        self.signals = OptimizationReviewSignals()
        self._sessions = sessions
        self._session_id = session_id
        self._recommendation_id = recommendation_id

    @Slot()
    def run(self) -> None:
        """Run bounded repository/path checks without blocking the desktop event loop."""
        try:
            result = self._sessions.prepare(self._session_id, self._recommendation_id)
        except Exception as exc:
            self.signals.failed.emit(type(exc).__name__)
            return
        self.signals.completed.emit(result)
