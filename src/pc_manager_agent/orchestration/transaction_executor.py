"""Fail-safe persistent executor for confirmed Stage 2A operation transactions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from uuid import UUID

from pydantic import JsonValue

from pc_manager_agent.audit.file_operations import OperationAuditLogger
from pc_manager_agent.confirmation.file_operations import OperationConfirmationService
from pc_manager_agent.domain.file_operations import (
    FileOperationPlan,
    FileOperationPreview,
    OperationPreviewItem,
    OperationType,
    PlannedFileOperation,
    PreviewItemStatus,
)
from pc_manager_agent.domain.transactions import (
    OperationExecutionReport,
    OperationItemState,
    OperationProgress,
    OperationTransaction,
    TransactionState,
)
from pc_manager_agent.persistence.file_operations import OperationRepository, OperationStoreError
from pc_manager_agent.rollback.models import UndoRecord
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest
from pc_manager_agent.tools.file_tools.operation_models import (
    CreateDirectoryRequest,
    FileOperationToolResult,
    MoveRequest,
    RenameRequest,
)
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry

ProgressCallback = Callable[[OperationProgress], None]


def build_operation_arguments(
    operation: PlannedFileOperation,
    preview_item: OperationPreviewItem,
) -> dict[str, JsonValue]:
    """Build exact validated tool arguments from the confirmed live Preview."""
    if operation.operation_type is OperationType.CREATE_DIRECTORY:
        return CreateDirectoryRequest(
            operation_id=operation.operation_id,
            destination=operation.destination,
        ).model_dump(mode="json")
    expected_state = preview_item.source_state or operation.expected_source_state
    if expected_state is None or operation.source is None:
        raise ValueError("Move or rename operation is missing source identity")
    if operation.operation_type in {
        OperationType.RENAME_FILE,
        OperationType.RENAME_DIRECTORY,
    }:
        return RenameRequest(
            operation_id=operation.operation_id,
            source=operation.source,
            destination=operation.destination,
            expected_source_state=expected_state,
            internal_temporary_path=operation.internal_temporary_path,
        ).model_dump(mode="json")
    return MoveRequest(
        operation_id=operation.operation_id,
        source=operation.source,
        destination=operation.destination,
        expected_source_state=expected_state,
    ).model_dump(mode="json")


def build_all_operation_arguments(
    plan: FileOperationPlan,
    preview: FileOperationPreview,
) -> dict[UUID, Mapping[str, JsonValue]]:
    """Build immutable argument reservations for every planned operation."""
    preview_by_id = {item.operation_id: item for item in preview.items}
    return {
        operation.operation_id: build_operation_arguments(
            operation, preview_by_id[operation.operation_id]
        )
        for operation in plan.operations
    }


class TransactionExecutor:
    """Execute only READY items, stopping future writes after any failure or cancellation."""

    def __init__(
        self,
        repository: OperationRepository,
        registry: ToolRegistry,
        confirmations: OperationConfirmationService,
        audit: OperationAuditLogger,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self._repository = repository
        self._registry = registry
        self._confirmations = confirmations
        self._audit = audit
        self._progress_callback = progress_callback

    def execute(
        self,
        plan: FileOperationPlan,
        preview: FileOperationPreview,
        confirmation_id: UUID,
        cancellation: CancellationToken | None = None,
    ) -> OperationExecutionReport:
        """Consume confirmation, persist RUNNING, execute, verify, and finalize each item."""
        token = cancellation or CancellationToken()
        consumed = self._confirmations.consume(confirmation_id, plan, preview)
        transaction = self._repository.get_transaction(preview.transaction_id)
        if (
            transaction.state is not TransactionState.CONFIRMED
            or transaction.plan_digest != plan.canonical_digest()
            or transaction.preview_digest != preview.canonical_digest()
        ):
            raise OperationStoreError("Persisted transaction does not match confirmed Preview")
        transaction = self._repository.transition(
            transaction.transaction_id, TransactionState.RUNNING
        )
        preview_by_id = {item.operation_id: item for item in preview.items}
        failed = False
        for operation in plan.operations:
            preview_item = preview_by_id[operation.operation_id]
            if preview_item.status is not PreviewItemStatus.READY:
                continue
            if token.is_cancelled:
                transaction = self._repository.transition(
                    transaction.transaction_id, TransactionState.CANCELLED
                )
                return self._report(transaction, preview)
            payload = build_operation_arguments(operation, preview_item)
            before_state = preview_item.source_state
            undo = UndoRecord(
                operation_id=operation.operation_id,
                transaction_id=transaction.transaction_id,
                sequence=operation.sequence,
                operation_type=operation.operation_type,
                original_path=operation.source,
                resulting_path=operation.destination,
                before_state=before_state,
                valid_when=(
                    "result identity matches verified after state",
                    "restore path remains absent",
                    "both paths remain authorized and non-reparse",
                ),
            )
            self._repository.begin_operation(
                transaction.transaction_id, operation.operation_id, undo
            )
            self._audit.operation_started(
                transaction,
                operation,
                str(consumed.confirmation_id),
                before_state,
            )
            authorization = ExecutionAuthorization(
                transaction_id=transaction.transaction_id,
                operation_id=operation.operation_id,
                plan_id=plan.plan_id,
                preview_id=preview.preview_id,
                tool_name=operation.tool_name,
                arguments_digest=arguments_digest(payload),
            )
            try:
                result = self._registry.execute(
                    operation.tool_name,
                    payload,
                    token,
                    authorization,
                )
                if not isinstance(result, FileOperationToolResult) or not result.verified:
                    raise RuntimeError("Registered file tool returned an unverified result")
                _item, available_undo = self._repository.complete_operation(
                    operation.operation_id, result.after_state
                )
                transaction = self._repository.get_transaction(transaction.transaction_id)
                self._audit.operation_completed(
                    transaction, operation, result.after_state, available_undo
                )
            except Exception as exc:
                failed = True
                with suppress(OperationStoreError):
                    self._repository.fail_operation(
                        operation.operation_id,
                        error_code=type(exc).__name__.upper(),
                        error_message=str(exc),
                    )
                # A verified mutation followed by a journal failure deliberately leaves
                # the PREPARED record and RUNNING item for startup reconciliation.
                transaction = self._repository.get_transaction(transaction.transaction_id)
                self._audit.operation_failed(transaction, operation, exc)
                break
            self._emit_progress(transaction, operation.destination)

        transaction = self._repository.get_transaction(transaction.transaction_id)
        if failed:
            terminal = (
                TransactionState.PARTIALLY_COMPLETED
                if transaction.completed_count
                else TransactionState.FAILED
            )
        else:
            terminal = TransactionState.COMPLETED
        transaction = self._repository.transition(transaction.transaction_id, terminal)
        return self._report(transaction, preview)

    def _emit_progress(
        self,
        transaction: OperationTransaction,
        current_path: Path,
    ) -> None:
        if self._progress_callback is None:
            return
        self._progress_callback(
            OperationProgress(
                transaction_id=transaction.transaction_id,
                current_sequence=transaction.completed_count + transaction.failed_count,
                operation_count=transaction.operation_count,
                completed_count=transaction.completed_count,
                failed_count=transaction.failed_count,
                skipped_count=transaction.skipped_count,
                current_path=current_path,
            )
        )

    def _report(
        self,
        transaction: OperationTransaction,
        preview: FileOperationPreview,
    ) -> OperationExecutionReport:
        items = self._repository.list_items(transaction.transaction_id)
        available = sum(item.state is OperationItemState.COMPLETED for item in items)
        return OperationExecutionReport(
            transaction=transaction,
            items=items,
            rollback_available_count=available,
            total_size_bytes=preview.total_size_bytes,
        )
