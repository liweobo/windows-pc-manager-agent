"""Durable, content-minimized Stage 5E task state and replay protection."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar, cast
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import Integer, String, Text, UniqueConstraint, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from pc_manager_agent.domain.computer_tasks import (
    ComputerTask,
    ComputerTaskEvent,
    ComputerTaskState,
    TaskPlanConfirmation,
    TaskPlanConfirmationState,
)
from pc_manager_agent.domain.task_attention import (
    UserAttentionItem,
    UserAttentionState,
)
from pc_manager_agent.domain.task_checkpoints import (
    PersistedTaskGraph,
    TaskCheckpoint,
    TaskDispatchState,
    TaskNodeDispatch,
)
from pc_manager_agent.domain.task_workflows import DomainResultReceipt
from pc_manager_agent.persistence.database import create_sqlite_engine

_SCHEMA_VERSION = 1
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class ComputerTaskStoreError(RuntimeError):
    """Raised when task state cannot be stored or integrity-checked."""


class DuplicateTaskDispatchError(ComputerTaskStoreError):
    """Raised when one graph-version node has already been dispatched."""


class ComputerTaskBase(DeclarativeBase):
    """Independent Stage 5E metadata schema."""


class TaskSchemaRow(ComputerTaskBase):
    """One schema version row used for fail-closed compatibility checks."""

    __tablename__ = "computer_task_schema"
    component: Mapped[str] = mapped_column(String(40), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class ComputerTaskRow(ComputerTaskBase):
    """Digest-protected root task payload without raw goal or Context."""

    __tablename__ = "computer_tasks"
    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(36), index=True)
    state: Mapped[str] = mapped_column(String(50), index=True)
    graph_version: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class ComputerTaskGraphRow(ComputerTaskBase):
    """Versioned graph structure; the raw goal is deliberately absent."""

    __tablename__ = "computer_task_graphs"
    graph_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    graph_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (UniqueConstraint("task_id", "graph_version"),)


class ComputerTaskCheckpointRow(ComputerTaskBase):
    """Append-only checkpoint payload."""

    __tablename__ = "computer_task_checkpoints"
    checkpoint_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    graph_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class ComputerTaskDispatchRow(ComputerTaskBase):
    """Unique node dispatch record used to stop UI/restart duplication."""

    __tablename__ = "computer_task_dispatches"
    dispatch_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    graph_version: Mapped[int] = mapped_column(Integer, nullable=False)
    node_id: Mapped[str] = mapped_column(String(36), nullable=False)
    state: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (UniqueConstraint("task_id", "graph_version", "node_id"),)


class TaskPlanConfirmationRow(ComputerTaskBase):
    """Task-plan-only consent; restart invalidates every pending or approved row."""

    __tablename__ = "computer_task_plan_confirmations"
    confirmation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    state: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class ComputerTaskAttentionRow(ComputerTaskBase):
    """Metadata-only user attention item."""

    __tablename__ = "computer_task_attention"
    attention_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    state: Mapped[str] = mapped_column(String(40), index=True)
    risk_level: Mapped[str] = mapped_column(String(30), index=True)
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class ComputerTaskReceiptRow(ComputerTaskBase):
    """One immutable owning-domain result receipt."""

    __tablename__ = "computer_task_receipts"
    receipt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    node_id: Mapped[str] = mapped_column(String(36), index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (UniqueConstraint("task_id", "node_id"),)


class ComputerTaskEventRow(ComputerTaskBase):
    """Append-only content-free lifecycle trace."""

    __tablename__ = "computer_task_events"
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    occurred_at: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)


def _encode(value: BaseModel) -> tuple[str, str]:
    payload = value.model_dump_json()
    return payload, hashlib.sha256(payload.encode()).hexdigest()


def _decode(payload: str, digest: str, model: type[_ModelT]) -> _ModelT:
    if hashlib.sha256(payload.encode()).hexdigest() != digest:
        raise ComputerTaskStoreError("Computer task payload digest mismatch")
    try:
        return model.model_validate_json(payload)
    except ValidationError as exc:
        raise ComputerTaskStoreError("Computer task payload schema mismatch") from exc


class ComputerTaskRepository:
    """Persist orchestration metadata while leaving execution truth in each domain."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        self._initialized = False

    def initialize(self) -> tuple[UUID, ...]:
        """Create/migrate tables, interrupt active work, and invalidate old consent."""
        interrupted: list[UUID] = []
        try:
            with self._engine.connect() as connection:
                if connection.execute(text("PRAGMA quick_check")).scalar_one() != "ok":
                    raise ComputerTaskStoreError("Computer task database integrity check failed")
            ComputerTaskBase.metadata.create_all(self._engine)
            now = datetime.now(UTC)
            with self._sessions.begin() as session:
                schema = session.get(TaskSchemaRow, "stage5e")
                if schema is None:
                    session.add(TaskSchemaRow(component="stage5e", version=_SCHEMA_VERSION))
                elif schema.version > _SCHEMA_VERSION:
                    raise ComputerTaskStoreError("Computer task schema is newer than this app")
                elif schema.version < _SCHEMA_VERSION:
                    schema.version = _SCHEMA_VERSION
                active = {
                    ComputerTaskState.CREATED.value,
                    ComputerTaskState.PLANNING.value,
                    ComputerTaskState.AWAITING_PLAN_CONFIRMATION.value,
                    ComputerTaskState.READY.value,
                    ComputerTaskState.RUNNING.value,
                    ComputerTaskState.WAITING_FOR_USER.value,
                    ComputerTaskState.WAITING_FOR_DOMAIN_CONFIRMATION.value,
                    ComputerTaskState.WAITING_FOR_USER_TAKEOVER.value,
                    ComputerTaskState.PAUSED.value,
                    ComputerTaskState.CANCELLING.value,
                    ComputerTaskState.RECOVERING.value,
                }
                rows = session.scalars(
                    select(ComputerTaskRow).where(ComputerTaskRow.state.in_(active))
                )
                for row in rows:
                    current = _decode(row.payload, row.payload_digest, ComputerTask)
                    changed = current.model_copy(
                        update={
                            "state": ComputerTaskState.INTERRUPTED,
                            "pause_requested": False,
                            "cancellation_requested": False,
                            "revision": current.revision + 1,
                            "updated_at": now,
                        }
                    )
                    self._write_task_row(row, changed)
                    interrupted.append(changed.task_id)
                confirmations = session.scalars(
                    select(TaskPlanConfirmationRow).where(
                        TaskPlanConfirmationRow.state.in_(
                            {
                                TaskPlanConfirmationState.PENDING.value,
                                TaskPlanConfirmationState.APPROVED.value,
                            }
                        )
                    )
                )
                for confirmation_row in confirmations:
                    current_confirmation = _decode(
                        confirmation_row.payload,
                        confirmation_row.payload_digest,
                        TaskPlanConfirmation,
                    )
                    changed_confirmation = current_confirmation.model_copy(
                        update={
                            "state": TaskPlanConfirmationState.INVALIDATED,
                            "resolved_at": now,
                        }
                    )
                    confirmation_row.state = changed_confirmation.state.value
                    confirmation_row.payload, confirmation_row.payload_digest = _encode(
                        changed_confirmation
                    )
                dispatches = session.scalars(
                    select(ComputerTaskDispatchRow).where(
                        ComputerTaskDispatchRow.state.in_(
                            {
                                TaskDispatchState.DISPATCHING.value,
                                TaskDispatchState.DISPATCHED.value,
                            }
                        )
                    )
                )
                for dispatch_row in dispatches:
                    current_dispatch = _decode(
                        dispatch_row.payload,
                        dispatch_row.payload_digest,
                        TaskNodeDispatch,
                    )
                    changed_dispatch = current_dispatch.model_copy(
                        update={"state": TaskDispatchState.RECONCILING, "updated_at": now}
                    )
                    dispatch_row.state = changed_dispatch.state.value
                    dispatch_row.payload, dispatch_row.payload_digest = _encode(changed_dispatch)
            self._initialized = True
        except ComputerTaskStoreError:
            raise
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Computer task initialization failed") from exc
        return tuple(interrupted)

    def create_task(self, task: ComputerTask, graph: PersistedTaskGraph) -> None:
        """Atomically create one root task and its first content-free graph."""
        self._require_initialized()
        if task.task_id != graph.task_id or task.graph_id != graph.graph_id:
            raise ComputerTaskStoreError("Task and graph identities disagree")
        task_payload, task_digest = _encode(task)
        graph_payload, graph_digest = _encode(graph)
        try:
            with self._sessions.begin() as session:
                session.add(
                    ComputerTaskRow(
                        task_id=str(task.task_id),
                        trace_id=str(task.trace_id),
                        state=task.state.value,
                        graph_version=task.graph_version,
                        revision=task.revision,
                        updated_at=task.updated_at.isoformat(),
                        payload=task_payload,
                        payload_digest=task_digest,
                    )
                )
                session.add(
                    ComputerTaskGraphRow(
                        graph_id=str(graph.graph_id),
                        task_id=str(graph.task_id),
                        graph_version=graph.version,
                        payload=graph_payload,
                        payload_digest=graph_digest,
                    )
                )
        except IntegrityError as exc:
            raise ComputerTaskStoreError("Computer task or graph already exists") from exc
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Computer task could not be created") from exc

    def get_task(self, task_id: UUID) -> ComputerTask:
        """Load and verify one root task."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(ComputerTaskRow, str(task_id))
                if row is None:
                    raise ComputerTaskStoreError("Unknown computer task")
                task = _decode(row.payload, row.payload_digest, ComputerTask)
                if (str(task.task_id), task.state.value, task.revision) != (
                    row.task_id,
                    row.state,
                    row.revision,
                ):
                    raise ComputerTaskStoreError("Computer task columns disagree")
                return task
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Computer task could not be read") from exc

    def save_task(self, task: ComputerTask, *, expected_revision: int) -> None:
        """Compare-and-swap one root state transition."""
        self._require_initialized()
        if task.revision != expected_revision + 1:
            raise ComputerTaskStoreError("Computer task revision is not consecutive")
        payload, digest = _encode(task)
        try:
            with self._sessions.begin() as session:
                result = cast(
                    "CursorResult[Any]",
                    session.execute(
                        update(ComputerTaskRow)
                        .where(
                            ComputerTaskRow.task_id == str(task.task_id),
                            ComputerTaskRow.revision == expected_revision,
                        )
                        .values(
                            state=task.state.value,
                            graph_version=task.graph_version,
                            revision=task.revision,
                            updated_at=task.updated_at.isoformat(),
                            payload=payload,
                            payload_digest=digest,
                        )
                    ),
                )
                if result.rowcount != 1:
                    raise ComputerTaskStoreError("Computer task changed concurrently")
        except ComputerTaskStoreError:
            raise
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Computer task could not be saved") from exc

    def save_graph(self, graph: PersistedTaskGraph) -> None:
        """Append one graph version without replacing prior task history."""
        self._require_initialized()
        payload, digest = _encode(graph)
        try:
            with self._sessions.begin() as session:
                session.add(
                    ComputerTaskGraphRow(
                        graph_id=str(graph.graph_id),
                        task_id=str(graph.task_id),
                        graph_version=graph.version,
                        payload=payload,
                        payload_digest=digest,
                    )
                )
        except IntegrityError as exc:
            raise ComputerTaskStoreError("Computer task graph version already exists") from exc
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Computer task graph could not be saved") from exc

    def get_graph(self, task_id: UUID, graph_version: int) -> PersistedTaskGraph:
        """Load a versioned graph structure without reconstructing its raw goal."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.scalar(
                    select(ComputerTaskGraphRow).where(
                        ComputerTaskGraphRow.task_id == str(task_id),
                        ComputerTaskGraphRow.graph_version == graph_version,
                    )
                )
                if row is None:
                    raise ComputerTaskStoreError("Unknown computer task graph")
                return _decode(row.payload, row.payload_digest, PersistedTaskGraph)
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Computer task graph could not be read") from exc

    def list_recent(self, limit: int = 100) -> tuple[ComputerTask, ...]:
        """Return recent verified task summaries for Task Center."""
        self._require_initialized()
        bounded = max(1, min(limit, 500))
        try:
            with self._sessions() as session:
                rows = session.scalars(
                    select(ComputerTaskRow)
                    .order_by(ComputerTaskRow.updated_at.desc())
                    .limit(bounded)
                )
                return tuple(_decode(row.payload, row.payload_digest, ComputerTask) for row in rows)
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Computer task listing failed") from exc

    def save_checkpoint(self, checkpoint: TaskCheckpoint) -> None:
        """Append and verify one durable checkpoint before later scheduling."""
        self._require_initialized()
        payload, digest = _encode(checkpoint)
        try:
            with self._sessions.begin() as session:
                session.add(
                    ComputerTaskCheckpointRow(
                        checkpoint_id=str(checkpoint.checkpoint_id),
                        task_id=str(checkpoint.task_id),
                        graph_version=checkpoint.graph_version,
                        created_at=checkpoint.created_at.isoformat(),
                        payload=payload,
                        payload_digest=digest,
                    )
                )
            loaded = self.latest_checkpoint(checkpoint.task_id)
            if loaded != checkpoint:
                raise ComputerTaskStoreError("Computer task checkpoint verification failed")
        except ComputerTaskStoreError:
            raise
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Computer task checkpoint could not be saved") from exc

    def latest_checkpoint(self, task_id: UUID) -> TaskCheckpoint:
        """Load the newest integrity-checked checkpoint."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.scalar(
                    select(ComputerTaskCheckpointRow)
                    .where(ComputerTaskCheckpointRow.task_id == str(task_id))
                    .order_by(ComputerTaskCheckpointRow.created_at.desc())
                    .limit(1)
                )
                if row is None:
                    raise ComputerTaskStoreError("Computer task has no checkpoint")
                return _decode(row.payload, row.payload_digest, TaskCheckpoint)
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Computer task checkpoint could not be read") from exc

    def reserve_dispatch(self, dispatch: TaskNodeDispatch) -> TaskNodeDispatch:
        """Atomically create one DISPATCHING row for a previously unseen node."""
        self._require_initialized()
        if dispatch.state is not TaskDispatchState.NOT_DISPATCHED or dispatch.attempt_count:
            raise ComputerTaskStoreError("New dispatch must be pristine")
        reserved = dispatch.model_copy(
            update={
                "state": TaskDispatchState.DISPATCHING,
                "attempt_count": 1,
                "updated_at": datetime.now(UTC),
            }
        )
        payload, digest = _encode(reserved)
        try:
            with self._sessions.begin() as session:
                session.add(
                    ComputerTaskDispatchRow(
                        dispatch_id=str(reserved.dispatch_id),
                        task_id=str(reserved.task_id),
                        graph_version=reserved.graph_version,
                        node_id=str(reserved.node_id),
                        state=reserved.state.value,
                        payload=payload,
                        payload_digest=digest,
                    )
                )
        except IntegrityError as exc:
            raise DuplicateTaskDispatchError("Computer task node was already dispatched") from exc
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Computer task dispatch could not be reserved") from exc
        return reserved

    def get_dispatch(self, dispatch_id: UUID) -> TaskNodeDispatch:
        """Load one integrity-checked dispatch record."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(ComputerTaskDispatchRow, str(dispatch_id))
                if row is None:
                    raise ComputerTaskStoreError("Unknown computer task dispatch")
                return _decode(row.payload, row.payload_digest, TaskNodeDispatch)
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Computer task dispatch could not be read") from exc

    def save_dispatch(self, dispatch: TaskNodeDispatch, expected: TaskDispatchState) -> None:
        """Advance one dispatch state without reopening it for duplicate execution."""
        self._require_initialized()
        payload, digest = _encode(dispatch)
        try:
            with self._sessions.begin() as session:
                result = cast(
                    "CursorResult[Any]",
                    session.execute(
                        update(ComputerTaskDispatchRow)
                        .where(
                            ComputerTaskDispatchRow.dispatch_id == str(dispatch.dispatch_id),
                            ComputerTaskDispatchRow.state == expected.value,
                        )
                        .values(
                            state=dispatch.state.value,
                            payload=payload,
                            payload_digest=digest,
                        )
                    ),
                )
                if result.rowcount != 1:
                    raise DuplicateTaskDispatchError("Computer task dispatch state changed")
        except ComputerTaskStoreError:
            raise
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Computer task dispatch could not be saved") from exc

    def unresolved_dispatches(self, task_id: UUID) -> tuple[TaskNodeDispatch, ...]:
        """Return dispatches that require result collection or Fresh reconciliation."""
        self._require_initialized()
        terminal = {TaskDispatchState.RESOLVED.value}
        try:
            with self._sessions() as session:
                rows = session.scalars(
                    select(ComputerTaskDispatchRow).where(
                        ComputerTaskDispatchRow.task_id == str(task_id),
                        ComputerTaskDispatchRow.state.not_in(terminal),
                    )
                )
                return tuple(
                    _decode(row.payload, row.payload_digest, TaskNodeDispatch) for row in rows
                )
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Computer task dispatch listing failed") from exc

    def save_plan_confirmation(self, confirmation: TaskPlanConfirmation) -> None:
        """Insert one task-plan consent without exposing it to domain execution."""
        self._require_initialized()
        payload, digest = _encode(confirmation)
        try:
            with self._sessions.begin() as session:
                session.add(
                    TaskPlanConfirmationRow(
                        confirmation_id=str(confirmation.confirmation_id),
                        task_id=str(confirmation.task_id),
                        state=confirmation.state.value,
                        payload=payload,
                        payload_digest=digest,
                    )
                )
        except IntegrityError as exc:
            raise ComputerTaskStoreError("Task plan confirmation already exists") from exc
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Task plan confirmation could not be saved") from exc

    def get_plan_confirmation(self, confirmation_id: UUID) -> TaskPlanConfirmation:
        """Load one integrity-checked task-plan confirmation."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                row = session.get(TaskPlanConfirmationRow, str(confirmation_id))
                if row is None:
                    raise ComputerTaskStoreError("Unknown task plan confirmation")
                return _decode(row.payload, row.payload_digest, TaskPlanConfirmation)
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Task plan confirmation could not be read") from exc

    def update_plan_confirmation(
        self,
        confirmation: TaskPlanConfirmation,
        *,
        expected: TaskPlanConfirmationState,
    ) -> None:
        """Resolve a task-plan confirmation once using state compare-and-swap."""
        self._require_initialized()
        payload, digest = _encode(confirmation)
        try:
            with self._sessions.begin() as session:
                result = cast(
                    "CursorResult[Any]",
                    session.execute(
                        update(TaskPlanConfirmationRow)
                        .where(
                            TaskPlanConfirmationRow.confirmation_id
                            == str(confirmation.confirmation_id),
                            TaskPlanConfirmationRow.state == expected.value,
                        )
                        .values(
                            state=confirmation.state.value,
                            payload=payload,
                            payload_digest=digest,
                        )
                    ),
                )
                if result.rowcount != 1:
                    raise ComputerTaskStoreError("Task plan confirmation changed or was replayed")
        except ComputerTaskStoreError:
            raise
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Task plan confirmation could not be updated") from exc

    def invalidate_plan_confirmations(
        self,
        task_id: UUID,
        *,
        now: datetime | None = None,
    ) -> int:
        """Invalidate unresolved task-plan consent after any graph revision."""
        self._require_initialized()
        observed = now or datetime.now(UTC)
        changed_count = 0
        try:
            with self._sessions.begin() as session:
                rows = session.scalars(
                    select(TaskPlanConfirmationRow).where(
                        TaskPlanConfirmationRow.task_id == str(task_id),
                        TaskPlanConfirmationRow.state.in_(
                            {
                                TaskPlanConfirmationState.PENDING.value,
                                TaskPlanConfirmationState.APPROVED.value,
                            }
                        ),
                    )
                )
                for row in rows:
                    current = _decode(row.payload, row.payload_digest, TaskPlanConfirmation)
                    changed = current.model_copy(
                        update={
                            "state": TaskPlanConfirmationState.INVALIDATED,
                            "resolved_at": observed,
                        }
                    )
                    row.state = changed.state.value
                    row.payload, row.payload_digest = _encode(changed)
                    changed_count += 1
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError(
                "Task plan confirmations could not be invalidated"
            ) from exc
        return changed_count

    def save_attention(self, item: UserAttentionItem) -> None:
        """Insert one content-minimized user attention item."""
        self._require_initialized()
        payload, digest = _encode(item)
        try:
            with self._sessions.begin() as session:
                session.add(
                    ComputerTaskAttentionRow(
                        attention_id=str(item.attention_id),
                        task_id=str(item.task_id),
                        state=item.state.value,
                        risk_level=item.risk_level.value,
                        created_at=item.created_at.isoformat(),
                        payload=payload,
                        payload_digest=digest,
                    )
                )
        except IntegrityError as exc:
            raise ComputerTaskStoreError("Task attention item already exists") from exc
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Task attention item could not be saved") from exc

    def update_attention(self, item: UserAttentionItem, expected: UserAttentionState) -> None:
        """Resolve or activate one attention item with compare-and-swap."""
        self._require_initialized()
        payload, digest = _encode(item)
        try:
            with self._sessions.begin() as session:
                result = cast(
                    "CursorResult[Any]",
                    session.execute(
                        update(ComputerTaskAttentionRow)
                        .where(
                            ComputerTaskAttentionRow.attention_id == str(item.attention_id),
                            ComputerTaskAttentionRow.state == expected.value,
                        )
                        .values(state=item.state.value, payload=payload, payload_digest=digest)
                    ),
                )
                if result.rowcount != 1:
                    raise ComputerTaskStoreError("Task attention item changed concurrently")
        except ComputerTaskStoreError:
            raise
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Task attention item could not be updated") from exc

    def list_attention(
        self,
        task_id: UUID,
        *,
        include_resolved: bool = False,
    ) -> tuple[UserAttentionItem, ...]:
        """Return task attention metadata in creation order."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                statement = select(ComputerTaskAttentionRow).where(
                    ComputerTaskAttentionRow.task_id == str(task_id)
                )
                if not include_resolved:
                    statement = statement.where(
                        ComputerTaskAttentionRow.state.in_(
                            {UserAttentionState.PENDING.value, UserAttentionState.ACTIVE.value}
                        )
                    )
                rows = session.scalars(statement.order_by(ComputerTaskAttentionRow.created_at))
                return tuple(
                    _decode(row.payload, row.payload_digest, UserAttentionItem) for row in rows
                )
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Task attention listing failed") from exc

    def list_all_attention(
        self,
        *,
        include_resolved: bool = False,
        limit: int = 200,
    ) -> tuple[UserAttentionItem, ...]:
        """Return bounded cross-task attention metadata for serialized UI presentation."""
        self._require_initialized()
        bounded = max(1, min(limit, 500))
        try:
            with self._sessions() as session:
                statement = select(ComputerTaskAttentionRow)
                if not include_resolved:
                    statement = statement.where(
                        ComputerTaskAttentionRow.state.in_(
                            {UserAttentionState.PENDING.value, UserAttentionState.ACTIVE.value}
                        )
                    )
                rows = session.scalars(
                    statement.order_by(ComputerTaskAttentionRow.created_at).limit(bounded)
                )
                return tuple(
                    _decode(row.payload, row.payload_digest, UserAttentionItem) for row in rows
                )
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Cross-task attention listing failed") from exc

    def save_receipt(self, receipt: DomainResultReceipt) -> None:
        """Persist one immutable domain-owned result for a task node."""
        self._require_initialized()
        payload, digest = _encode(receipt)
        try:
            with self._sessions.begin() as session:
                session.add(
                    ComputerTaskReceiptRow(
                        receipt_id=str(receipt.receipt_id),
                        task_id=str(receipt.task_id),
                        node_id=str(receipt.node_id),
                        payload=payload,
                        payload_digest=digest,
                    )
                )
        except IntegrityError as exc:
            raise ComputerTaskStoreError("Task node already has a domain receipt") from exc
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Domain receipt could not be saved") from exc

    def list_receipts(self, task_id: UUID) -> tuple[DomainResultReceipt, ...]:
        """Return all integrity-checked domain receipts for deterministic summary."""
        self._require_initialized()
        try:
            with self._sessions() as session:
                rows = session.scalars(
                    select(ComputerTaskReceiptRow).where(
                        ComputerTaskReceiptRow.task_id == str(task_id)
                    )
                )
                return tuple(
                    _decode(row.payload, row.payload_digest, DomainResultReceipt) for row in rows
                )
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Domain receipt listing failed") from exc

    def append_event(self, event: ComputerTaskEvent) -> None:
        """Append one metadata-only task event."""
        self._require_initialized()
        payload, digest = _encode(event)
        try:
            with self._sessions.begin() as session:
                session.add(
                    ComputerTaskEventRow(
                        event_id=str(event.event_id),
                        task_id=str(event.task_id),
                        occurred_at=event.occurred_at.isoformat(),
                        payload=payload,
                        payload_digest=digest,
                    )
                )
        except SQLAlchemyError as exc:
            raise ComputerTaskStoreError("Computer task event could not be saved") from exc

    def close(self) -> None:
        """Release database connections without deleting history or recovery evidence."""
        self._engine.dispose()

    @staticmethod
    def _write_task_row(row: ComputerTaskRow, task: ComputerTask) -> None:
        payload, digest = _encode(task)
        row.state = task.state.value
        row.graph_version = task.graph_version
        row.revision = task.revision
        row.updated_at = task.updated_at.isoformat()
        row.payload = payload
        row.payload_digest = digest

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise ComputerTaskStoreError("Computer task repository is not initialized")
