"""Typed audit adapter for Stage 2A Preview, execution, and rollback events."""

from __future__ import annotations

from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.file_operations import (
    OperationConfirmation,
    RollbackConfirmation,
)
from pc_manager_agent.domain.file_operations import (
    FileOperationPlan,
    FileOperationPreview,
    FileState,
    PlannedFileOperation,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.transactions import OperationTransaction
from pc_manager_agent.rollback.models import RollbackPlan, UndoRecord


class OperationAuditLogger:
    """Append redacted local audit events without conflating them with Undo records."""

    def __init__(
        self,
        repository: AuditRepository,
        *,
        app_version: str,
        git_commit: str | None,
    ) -> None:
        self._repository = repository
        self._app_version = app_version
        self._git_commit = git_commit

    def previewed(self, plan: FileOperationPlan, preview: FileOperationPreview) -> None:
        """Record a reviewed concrete plan and its read-only live Preview."""
        self._repository.record(
            AuditEvent(
                event_type="file_operation.previewed",
                original_request=plan.user_goal,
                plan=plan.model_dump(mode="json"),
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                agent_decision="R1 Preview generated; no mutation executed",
                parameters={
                    "transaction_id": str(preview.transaction_id),
                    "preview_id": str(preview.preview_id),
                    "preview_digest": preview.canonical_digest(),
                    "operation_count": len(preview.items),
                    "ready_count": preview.ready_count,
                    "conflict_count": preview.conflict_count,
                    "blocked_count": preview.blocked_count,
                },
                risk_level=RiskLevel.R1,
                confirmation_required=True,
                result={"total_size_bytes": preview.total_size_bytes},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def confirmation_resolved(
        self,
        plan: FileOperationPlan,
        confirmation: OperationConfirmation,
    ) -> None:
        """Record the exact confirmation decision and bindings."""
        self._repository.record(
            AuditEvent(
                event_type="file_operation.confirmation_resolved",
                original_request=plan.user_goal,
                plan_id=str(plan.plan_id),
                plan_version=plan.plan_version,
                parameters={
                    "confirmation_id": str(confirmation.confirmation_id),
                    "transaction_id": str(confirmation.transaction_id),
                    "preview_id": str(confirmation.preview_id),
                    "plan_digest": confirmation.plan_digest,
                    "preview_digest": confirmation.preview_digest,
                    "operation_count": confirmation.operation_count,
                    "confirmed_at": (
                        confirmation.confirmed_at.isoformat()
                        if confirmation.confirmed_at is not None
                        else None
                    ),
                },
                risk_level=RiskLevel.R1,
                confirmation_required=True,
                confirmation_result=confirmation.state.value,
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def operation_started(
        self,
        transaction: OperationTransaction,
        operation: PlannedFileOperation,
        confirmation_id: str,
        before_state: FileState | None,
    ) -> None:
        """Fail closed before mutation if the mandatory audit store is unavailable."""
        self._repository.record(
            AuditEvent(
                event_type="file_operation.started",
                plan_id=str(transaction.plan_id),
                step_id=str(operation.operation_id),
                tool_name=operation.tool_name,
                parameters={
                    "transaction_id": str(transaction.transaction_id),
                    "preview_id": str(transaction.preview_id),
                    "operation_type": operation.operation_type.value,
                    "source": str(operation.source) if operation.source else None,
                    "destination": str(operation.destination),
                    "confirmation_id": confirmation_id,
                },
                risk_level=RiskLevel.R1,
                confirmation_required=True,
                confirmation_result="CONSUMED",
                before_state=(before_state.model_dump(mode="json") if before_state else None),
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def operation_completed(
        self,
        transaction: OperationTransaction,
        operation: PlannedFileOperation,
        after_state: FileState,
        undo: UndoRecord,
    ) -> None:
        """Record verified success and reference the separate Undo record."""
        self._repository.record(
            AuditEvent(
                event_type="file_operation.completed",
                plan_id=str(transaction.plan_id),
                step_id=str(operation.operation_id),
                tool_name=operation.tool_name,
                parameters={
                    "transaction_id": str(transaction.transaction_id),
                    "preview_id": str(transaction.preview_id),
                    "operation_type": operation.operation_type.value,
                    "source": str(operation.source) if operation.source else None,
                    "destination": str(operation.destination),
                },
                risk_level=RiskLevel.R1,
                confirmation_required=True,
                confirmation_result="CONSUMED",
                result={"execution_result": "COMPLETED"},
                after_state=after_state.model_dump(mode="json"),
                rollback={
                    "undo_record_id": str(undo.undo_id),
                    "rollback_level": undo.rollback_level.value,
                    "status": undo.status.value,
                },
                verification={"verified": True, "identity_preserved": True},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def operation_failed(
        self,
        transaction: OperationTransaction,
        operation: PlannedFileOperation,
        error: Exception,
    ) -> None:
        """Record a sanitized explicit failure without claiming rollback success."""
        self._repository.record(
            AuditEvent(
                event_type="file_operation.failed",
                plan_id=str(transaction.plan_id),
                step_id=str(operation.operation_id),
                tool_name=operation.tool_name,
                parameters={
                    "transaction_id": str(transaction.transaction_id),
                    "preview_id": str(transaction.preview_id),
                    "operation_type": operation.operation_type.value,
                    "source": str(operation.source) if operation.source else None,
                    "destination": str(operation.destination),
                },
                risk_level=RiskLevel.R1,
                confirmation_required=True,
                error={"type": type(error).__name__, "message": str(error)},
                verification={"verified": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def rollback_result(
        self,
        transaction: OperationTransaction,
        undo: UndoRecord,
        *,
        success: bool,
        message: str,
    ) -> None:
        """Record one verified or failed rollback attempt."""
        self._repository.record(
            AuditEvent(
                event_type=(
                    "file_operation.rollback_completed"
                    if success
                    else "file_operation.rollback_failed"
                ),
                plan_id=str(transaction.plan_id),
                step_id=str(undo.operation_id),
                parameters={
                    "transaction_id": str(transaction.transaction_id),
                    "undo_record_id": str(undo.undo_id),
                    "operation_type": undo.operation_type.value,
                    "current_path": str(undo.resulting_path),
                    "restore_path": str(undo.original_path) if undo.original_path else None,
                },
                risk_level=RiskLevel.R1,
                confirmation_required=True,
                result={"success": success, "message": message},
                rollback={"rollback_level": undo.rollback_level.value},
                verification={"verified": success},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def rollback_previewed(
        self,
        transaction: OperationTransaction,
        plan: RollbackPlan,
    ) -> None:
        """Record a read-only rollback assessment separately from its Undo payloads."""
        self._repository.record(
            AuditEvent(
                event_type="file_operation.rollback_previewed",
                plan_id=str(transaction.plan_id),
                parameters={
                    "transaction_id": str(transaction.transaction_id),
                    "rollback_plan_id": str(plan.rollback_plan_id),
                    "rollback_digest": plan.canonical_digest(),
                    "ready_count": sum(item.status.value == "READY" for item in plan.items),
                    "conflict_count": sum(item.status.value == "CONFLICT" for item in plan.items),
                    "blocked_count": sum(item.status.value == "BLOCKED" for item in plan.items),
                },
                risk_level=RiskLevel.R1,
                confirmation_required=True,
                result={"mutation_executed": False},
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )

    def rollback_confirmation_resolved(
        self,
        transaction: OperationTransaction,
        confirmation: RollbackConfirmation,
    ) -> None:
        """Record the independent rollback approval or rejection and exact digest."""
        self._repository.record(
            AuditEvent(
                event_type="file_operation.rollback_confirmation_resolved",
                plan_id=str(transaction.plan_id),
                parameters={
                    "transaction_id": str(transaction.transaction_id),
                    "rollback_plan_id": str(confirmation.rollback_plan_id),
                    "confirmation_id": str(confirmation.confirmation_id),
                    "rollback_digest": confirmation.rollback_digest,
                    "confirmed_at": (
                        confirmation.confirmed_at.isoformat()
                        if confirmation.confirmed_at is not None
                        else None
                    ),
                },
                risk_level=RiskLevel.R1,
                confirmation_required=True,
                confirmation_result=confirmation.state.value,
                app_version=self._app_version,
                git_commit=self._git_commit,
            )
        )
