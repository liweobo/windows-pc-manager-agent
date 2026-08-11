"""Rollback Preview, confirmation, reverse execution, and verification manager."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import JsonValue

from pc_manager_agent.audit.file_operations import OperationAuditLogger
from pc_manager_agent.confirmation.file_operations import (
    RollbackConfirmation,
    RollbackConfirmationService,
)
from pc_manager_agent.domain.file_operations import (
    FileObjectKind,
    OperationPreviewIssue,
    OperationType,
)
from pc_manager_agent.domain.transactions import OperationTransaction, TransactionState
from pc_manager_agent.persistence.file_operations import OperationRepository
from pc_manager_agent.platform_support.base import FileOperationPlatform
from pc_manager_agent.rollback.models import (
    RollbackItemStatus,
    RollbackPlan,
    RollbackPreviewItem,
    UndoRecord,
    UndoStatus,
)
from pc_manager_agent.safety.path_policy import PathPolicy, PathSecurityError
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest
from pc_manager_agent.tools.file_tools.operation_models import (
    EmptyDirectoryRollbackResult,
    FileOperationToolResult,
    MoveRequest,
    RemoveCreatedDirectoryRequest,
    RenameRequest,
)
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class PreparedRollback:
    """Live rollback plan and pending one-time confirmation."""

    plan: RollbackPlan
    confirmation: RollbackConfirmation | None


class RollbackManager:
    """Generate rollback only from durable Undo data, never model-guessed reverse paths."""

    def __init__(
        self,
        repository: OperationRepository,
        path_policy: PathPolicy,
        platform: FileOperationPlatform,
        registry: ToolRegistry,
        confirmations: RollbackConfirmationService,
        audit: OperationAuditLogger,
    ) -> None:
        self._repository = repository
        self._path_policy = path_policy
        self._platform = platform
        self._registry = registry
        self._confirmations = confirmations
        self._audit = audit

    def prepare(self, transaction_id: UUID) -> PreparedRollback:
        """Build a reverse-ordered live Preview and request exact rollback confirmation."""
        transaction = self._repository.get_transaction(transaction_id)
        if transaction.state not in {
            TransactionState.COMPLETED,
            TransactionState.PARTIALLY_COMPLETED,
            TransactionState.FAILED,
            TransactionState.CANCELLED,
            TransactionState.INTERRUPTED,
            TransactionState.PARTIALLY_ROLLED_BACK,
            TransactionState.ROLLBACK_FAILED,
        }:
            raise RuntimeError(f"Transaction cannot be rolled back from {transaction.state.value}")
        items: list[RollbackPreviewItem] = []
        paths_vacated_by_earlier_reverse_steps: set[str] = set()
        for record in self._repository.list_undo(transaction_id):
            item = self._preview_undo(record, paths_vacated_by_earlier_reverse_steps)
            items.append(item)
            if item.status is RollbackItemStatus.READY:
                paths_vacated_by_earlier_reverse_steps.add(
                    os.path.normcase(os.fspath(item.current_path))
                )
        plan = RollbackPlan(transaction_id=transaction_id, items=items)
        self._audit.rollback_previewed(transaction, plan)
        confirmation = (
            self._confirmations.request(plan)
            if any(item.status is RollbackItemStatus.READY for item in items)
            else None
        )
        return PreparedRollback(plan=plan, confirmation=confirmation)

    def resolve_confirmation(
        self,
        prepared: PreparedRollback,
        approved: bool,
    ) -> RollbackConfirmation:
        """Resolve the exact rollback Preview without changing transaction state yet."""
        if prepared.confirmation is None:
            raise RuntimeError("Rollback Preview has no safe item to confirm")
        resolved = self._confirmations.resolve(
            prepared.confirmation.confirmation_id,
            approved,
            prepared.plan,
        )
        transaction = self._repository.get_transaction(prepared.plan.transaction_id)
        self._audit.rollback_confirmation_resolved(transaction, resolved)
        return resolved

    def execute(
        self,
        prepared: PreparedRollback,
        cancellation: CancellationToken | None = None,
    ) -> OperationTransaction:
        """Consume confirmation and execute safe reverse operations in descending sequence."""
        if prepared.confirmation is None:
            raise RuntimeError("Rollback Preview has no safe item to execute")
        token = cancellation or CancellationToken()
        self._confirmations.consume(prepared.confirmation.confirmation_id, prepared.plan)
        transaction = self._repository.transition(
            prepared.plan.transaction_id, TransactionState.ROLLING_BACK
        )
        succeeded = 0
        failed = False
        ready_items = [
            item for item in prepared.plan.items if item.status is RollbackItemStatus.READY
        ]
        for item in ready_items:
            if token.is_cancelled:
                break
            undo = self._repository.get_undo(item.operation_id)
            operation = self._repository.get_operation(item.operation_id)
            began = False
            try:
                tool_name, payload = self._build_reverse_arguments(
                    undo, operation.internal_temporary_path
                )
                self._repository.begin_rollback_operation(
                    undo.operation_id,
                    tool_name=tool_name,
                    argument_payload=payload,
                )
                began = True
                authorization = ExecutionAuthorization(
                    transaction_id=transaction.transaction_id,
                    operation_id=undo.operation_id,
                    plan_id=transaction.plan_id,
                    preview_id=transaction.preview_id,
                    tool_name=tool_name,
                    arguments_digest=arguments_digest(payload),
                )
                result = self._registry.execute(
                    tool_name,
                    payload,
                    token,
                    authorization,
                )
                self._verify_reverse_result(undo, result)
                self._repository.mark_item_rolled_back(
                    undo.operation_id,
                    success=True,
                    result="Verified rollback completed",
                )
                succeeded += 1
                self._audit.rollback_result(
                    transaction,
                    undo,
                    success=True,
                    message="Verified rollback completed",
                )
            except Exception as exc:
                failed = True
                if began:
                    self._repository.mark_item_rolled_back(
                        undo.operation_id,
                        success=False,
                        result=f"{type(exc).__name__}: {exc}",
                    )
                self._audit.rollback_result(
                    transaction,
                    undo,
                    success=False,
                    message=str(exc),
                )
                break

        if failed and succeeded == 0:
            terminal = TransactionState.ROLLBACK_FAILED
        elif (
            failed
            or succeeded < len(ready_items)
            or any(item.status is not RollbackItemStatus.READY for item in prepared.plan.items)
        ):
            terminal = TransactionState.PARTIALLY_ROLLED_BACK
        else:
            terminal = TransactionState.ROLLED_BACK
        return self._repository.transition(transaction.transaction_id, terminal)

    def _preview_undo(
        self,
        undo: UndoRecord,
        paths_vacated_by_earlier_reverse_steps: set[str],
    ) -> RollbackPreviewItem:
        """Revalidate one Undo record and classify conflicts without mutating anything."""
        issues: list[OperationPreviewIssue] = []
        status = RollbackItemStatus.READY
        current_state = None
        if undo.status not in {
            UndoStatus.AVAILABLE,
            UndoStatus.PREPARED,
            UndoStatus.ROLLBACK_FAILED,
        }:
            status = RollbackItemStatus.BLOCKED
            issues.append(
                OperationPreviewIssue(
                    code="UNDO_UNAVAILABLE",
                    message=f"Undo record is {undo.status.value}",
                )
            )
        try:
            current_path = self._path_policy.validate_operation_source(undo.resulting_path)
            current_state = self._platform.inspect(current_path)
        except (OSError, PathSecurityError) as exc:
            current_path = undo.resulting_path
            status = RollbackItemStatus.BLOCKED
            issues.append(OperationPreviewIssue(code="RESULT_UNAVAILABLE", message=str(exc)))

        expected = undo.after_state or undo.before_state
        if current_state is not None:
            unchanged = expected is not None and (
                current_state.identity_matches(expected)
                if undo.operation_type is OperationType.CREATE_DIRECTORY
                else current_state.unchanged_since(expected)
            )
            if not unchanged:
                status = RollbackItemStatus.CONFLICT
                issues.append(
                    OperationPreviewIssue(
                        code="RESULT_CHANGED",
                        message="The resulting object changed after the operation",
                    )
                )
            if undo.operation_type is OperationType.CREATE_DIRECTORY:
                if current_state.kind is not FileObjectKind.DIRECTORY:
                    status = RollbackItemStatus.CONFLICT
                    issues.append(
                        OperationPreviewIssue(
                            code="CREATED_OBJECT_CHANGED",
                            message="The created object is no longer a directory",
                        )
                    )
                elif any(
                    os.path.normcase(os.fspath(child)) not in paths_vacated_by_earlier_reverse_steps
                    for child in current_path.iterdir()
                ):
                    status = RollbackItemStatus.CONFLICT
                    issues.append(
                        OperationPreviewIssue(
                            code="DIRECTORY_NOT_EMPTY",
                            message="The created directory now contains other objects",
                        )
                    )

        if undo.original_path is not None:
            try:
                restore = self._path_policy.validate_operation_destination(undo.original_path)
                if restore.exists() and current_state is not None:
                    existing = self._platform.inspect(restore)
                    case_only_same = (
                        current_state.identity_matches(existing)
                        and restore.parent == current_path.parent
                        and restore.name.casefold() == current_path.name.casefold()
                    )
                    if not case_only_same:
                        status = RollbackItemStatus.CONFLICT
                        issues.append(
                            OperationPreviewIssue(
                                code="ROLLBACK_CONFLICT",
                                message="The original path is occupied by another object",
                            )
                        )
            except (OSError, PathSecurityError) as exc:
                restore = undo.original_path
                status = RollbackItemStatus.BLOCKED
                issues.append(OperationPreviewIssue(code="RESTORE_PATH_UNSAFE", message=str(exc)))
        else:
            restore = None
        return RollbackPreviewItem(
            undo_id=undo.undo_id,
            operation_id=undo.operation_id,
            sequence=undo.sequence,
            status=status,
            current_path=current_path,
            restore_path=restore,
            current_state=current_state,
            issues=tuple(issues),
        )

    def _build_reverse_arguments(
        self,
        undo: UndoRecord,
        temporary_path: Path | None,
    ) -> tuple[str, dict[str, JsonValue]]:
        """Build reverse registered-tool arguments from persistent Undo and current identity."""
        current = self._platform.inspect(
            self._path_policy.validate_operation_source(undo.resulting_path)
        )
        expected = undo.after_state or undo.before_state
        unchanged = expected is not None and (
            current.identity_matches(expected)
            if undo.operation_type is OperationType.CREATE_DIRECTORY
            else current.unchanged_since(expected)
        )
        if not unchanged:
            raise RuntimeError("Rollback source changed after confirmation")
        if undo.operation_type is OperationType.CREATE_DIRECTORY:
            payload = RemoveCreatedDirectoryRequest(
                operation_id=undo.operation_id,
                path=undo.resulting_path,
                expected_state=current,
            ).model_dump(mode="json")
            return "file.rollback.rmdir-empty", payload
        if undo.original_path is None:
            raise RuntimeError("Move or rename Undo is missing the original path")
        if undo.operation_type in {
            OperationType.RENAME_FILE,
            OperationType.RENAME_DIRECTORY,
        }:
            payload = RenameRequest(
                operation_id=undo.operation_id,
                source=undo.resulting_path,
                destination=undo.original_path,
                expected_source_state=current,
                internal_temporary_path=temporary_path,
            ).model_dump(mode="json")
            return "file.rename", payload
        payload = MoveRequest(
            operation_id=undo.operation_id,
            source=undo.resulting_path,
            destination=undo.original_path,
            expected_source_state=current,
        ).model_dump(mode="json")
        return "file.move", payload

    def _verify_reverse_result(self, undo: UndoRecord, result: object) -> None:
        """Verify the registered rollback tool returned the expected typed postcondition."""
        if undo.operation_type is OperationType.CREATE_DIRECTORY:
            if not isinstance(result, EmptyDirectoryRollbackResult) or not result.verified:
                raise RuntimeError("Empty-directory rollback returned an invalid result")
            if undo.resulting_path.exists():
                raise RuntimeError("Created directory still exists after rollback")
            return
        if not isinstance(result, FileOperationToolResult) or not result.verified:
            raise RuntimeError("Move or rename rollback returned an invalid result")
        expected = undo.after_state or undo.before_state
        if expected is None or not result.after_state.identity_matches(expected):
            raise RuntimeError("Rollback restored an unexpected filesystem identity")
