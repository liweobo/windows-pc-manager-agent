"""Explicit Stage 5E root-task lifecycle transitions."""

from __future__ import annotations

from datetime import UTC, datetime

from pc_manager_agent.domain.computer_tasks import ComputerTask, ComputerTaskState


class TaskStateTransitionError(RuntimeError):
    """Raised when a caller attempts an unsafe or ambiguous lifecycle transition."""


_TERMINAL = {
    ComputerTaskState.CANCELLED,
    ComputerTaskState.PARTIALLY_COMPLETED,
    ComputerTaskState.COMPLETED,
    ComputerTaskState.FAILED,
    ComputerTaskState.BLOCKED,
}

_TRANSITIONS: dict[ComputerTaskState, frozenset[ComputerTaskState]] = {
    ComputerTaskState.CREATED: frozenset({ComputerTaskState.PLANNING, ComputerTaskState.CANCELLED}),
    ComputerTaskState.PLANNING: frozenset(
        {
            ComputerTaskState.AWAITING_PLAN_CONFIRMATION,
            ComputerTaskState.BLOCKED,
            ComputerTaskState.FAILED,
            ComputerTaskState.CANCELLED,
        }
    ),
    ComputerTaskState.AWAITING_PLAN_CONFIRMATION: frozenset(
        {
            ComputerTaskState.PLANNING,
            ComputerTaskState.READY,
            ComputerTaskState.CANCELLED,
            ComputerTaskState.BLOCKED,
        }
    ),
    ComputerTaskState.READY: frozenset(
        {
            ComputerTaskState.RUNNING,
            ComputerTaskState.PAUSED,
            ComputerTaskState.CANCELLED,
        }
    ),
    ComputerTaskState.RUNNING: frozenset(
        {
            ComputerTaskState.WAITING_FOR_USER,
            ComputerTaskState.WAITING_FOR_DOMAIN_CONFIRMATION,
            ComputerTaskState.WAITING_FOR_USER_TAKEOVER,
            ComputerTaskState.PAUSED,
            ComputerTaskState.CANCELLING,
            ComputerTaskState.PARTIALLY_COMPLETED,
            ComputerTaskState.COMPLETED,
            ComputerTaskState.FAILED,
            ComputerTaskState.BLOCKED,
            ComputerTaskState.INTERRUPTED,
        }
    ),
    ComputerTaskState.WAITING_FOR_USER: frozenset(
        {
            ComputerTaskState.RUNNING,
            ComputerTaskState.PLANNING,
            ComputerTaskState.PAUSED,
            ComputerTaskState.CANCELLING,
            ComputerTaskState.CANCELLED,
            ComputerTaskState.BLOCKED,
        }
    ),
    ComputerTaskState.WAITING_FOR_DOMAIN_CONFIRMATION: frozenset(
        {
            ComputerTaskState.RUNNING,
            ComputerTaskState.PLANNING,
            ComputerTaskState.PAUSED,
            ComputerTaskState.CANCELLING,
            ComputerTaskState.CANCELLED,
            ComputerTaskState.BLOCKED,
        }
    ),
    ComputerTaskState.WAITING_FOR_USER_TAKEOVER: frozenset(
        {
            ComputerTaskState.RUNNING,
            ComputerTaskState.PLANNING,
            ComputerTaskState.PAUSED,
            ComputerTaskState.CANCELLING,
            ComputerTaskState.CANCELLED,
            ComputerTaskState.BLOCKED,
        }
    ),
    ComputerTaskState.PAUSED: frozenset(
        {
            ComputerTaskState.RUNNING,
            ComputerTaskState.PLANNING,
            ComputerTaskState.CANCELLING,
            ComputerTaskState.CANCELLED,
            ComputerTaskState.INTERRUPTED,
        }
    ),
    ComputerTaskState.CANCELLING: frozenset(
        {
            ComputerTaskState.CANCELLED,
            ComputerTaskState.PARTIALLY_COMPLETED,
            ComputerTaskState.INTERRUPTED,
        }
    ),
    ComputerTaskState.INTERRUPTED: frozenset(
        {ComputerTaskState.RECOVERING, ComputerTaskState.CANCELLED}
    ),
    ComputerTaskState.RECOVERING: frozenset(
        {
            ComputerTaskState.WAITING_FOR_USER,
            ComputerTaskState.PARTIALLY_COMPLETED,
            ComputerTaskState.BLOCKED,
            ComputerTaskState.FAILED,
            ComputerTaskState.CANCELLED,
        }
    ),
}


class TaskStateMachine:
    """Apply one compare-and-swap-friendly immutable state transition."""

    def transition(
        self,
        task: ComputerTask,
        target: ComputerTaskState,
        *,
        now: datetime | None = None,
    ) -> ComputerTask:
        """Return a new task state or fail without changing the supplied task."""
        if task.state in _TERMINAL:
            raise TaskStateTransitionError("Terminal task state cannot change")
        if target not in _TRANSITIONS.get(task.state, frozenset()):
            raise TaskStateTransitionError(
                f"Task transition {task.state.value} -> {target.value} is not allowed"
            )
        observed = now or datetime.now(UTC)
        updates: dict[str, object] = {
            "state": target,
            "revision": task.revision + 1,
            "updated_at": observed,
        }
        if target is ComputerTaskState.RUNNING and task.started_at is None:
            updates["started_at"] = observed
        if target in _TERMINAL:
            updates["completed_at"] = observed
            updates["current_node_id"] = None
        return task.model_copy(update=updates)

    @staticmethod
    def is_terminal(state: ComputerTaskState) -> bool:
        """Return whether no further lifecycle transition is permitted."""
        return state in _TERMINAL
