"""Stage 2B workflow coordinating review, Preview, two confirmations, and execution."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import JsonValue

from pc_manager_agent.audit.trash import TrashAuditLogger
from pc_manager_agent.confirmation.trash import (
    TrashConfirmation,
    TrashConfirmationService,
)
from pc_manager_agent.domain.transactions import TransactionState
from pc_manager_agent.domain.trash import (
    RecycleBinResult,
    RecycleVerificationStatus,
    TrashExecutionReport,
    TrashPlan,
    TrashPreview,
    TrashPreviewStatus,
)
from pc_manager_agent.persistence.file_operations import OperationRepository, OperationStoreError
from pc_manager_agent.recovery.models import RecoveryStatus, TrashRecoveryRecord
from pc_manager_agent.safety.plan_reviewer import SafetyReview
from pc_manager_agent.safety.trash_preview import TrashPreviewEngine
from pc_manager_agent.safety.trash_validator import TrashSafetyValidator
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest
from pc_manager_agent.tools.file_tools.trash import TrashRequest, TrashResult
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class PreparedTrashOperation:
    """Reviewed R2 Preview and pending first-level confirmation returned to UI."""

    plan: TrashPlan
    review: SafetyReview
    preview: TrashPreview
    plan_confirmation: TrashConfirmation


@dataclass(frozen=True, slots=True)
class RuntimeConfirmedTrashOperation:
    """Freshly revalidated Preview and pending immediate confirmation."""

    prepared: PreparedTrashOperation
    runtime_preview: TrashPreview
    runtime_confirmation: TrashConfirmation


def build_trash_arguments(
    plan: TrashPlan, preview: TrashPreview
) -> dict[UUID, dict[str, JsonValue]]:
    """Reserve exact identities and complete tree snapshots for every selected item."""
    preview_by_id = {item.operation_id: item for item in preview.items}
    arguments: dict[UUID, dict[str, JsonValue]] = {}
    for item in plan.items:
        preview_item = preview_by_id[item.operation_id]
        if preview_item.snapshot is None:
            raise ValueError(f"Trash item lacks a verified snapshot: {item.source}")
        arguments[item.operation_id] = TrashRequest(
            source=item.source,
            expected_source_state=preview_item.snapshot.root_state,
            expected_snapshot=preview_item.snapshot,
        ).model_dump(mode="json")
    return arguments


class TrashService:
    """Keep UI and model providers outside all R2 execution boundaries."""

    def __init__(
        self,
        validator: TrashSafetyValidator,
        preview_engine: TrashPreviewEngine,
        confirmations: TrashConfirmationService,
        repository: OperationRepository,
        registry: ToolRegistry,
        audit: TrashAuditLogger,
    ) -> None:
        self._validator = validator
        self._preview = preview_engine
        self._confirmations = confirmations
        self._repository = repository
        self._registry = registry
        self._audit = audit

    def prepare(self, plan: TrashPlan) -> PreparedTrashOperation:
        """Review, Preview, journal, and request the first confirmation without writes."""
        review = self._validator.review(plan)
        if not review.approved:
            details = "; ".join(issue.message for issue in review.issues)
            raise PermissionError(f"Trash safety review denied the plan: {details}")
        preview = self._preview.generate(plan)
        if preview.ready_count != preview.selected_count or preview.blocked_count:
            raise PermissionError("Every explicitly selected object must be safe to continue")
        arguments = build_trash_arguments(plan, preview)
        self._repository.create_trash_from_preview(plan, preview, arguments)
        self._repository.transition(preview.transaction_id, TransactionState.AWAITING_CONFIRMATION)
        confirmation = self._confirmations.request_plan(plan, preview)
        self._repository.record_confirmation(
            preview.transaction_id,
            confirmation_id=confirmation.confirmation_id,
            tier=confirmation.tier.value,
            state=confirmation.state.value,
            binding_digest=confirmation.preview_digest,
            confirmed_at=None,
        )
        self._audit.previewed(plan, preview)
        return PreparedTrashOperation(plan, review, preview, confirmation)

    def resolve_plan_confirmation(
        self,
        prepared: PreparedTrashOperation,
        approved: bool,
    ) -> TrashConfirmation:
        """Persist the first decision; approval still cannot start execution."""
        confirmation = self._confirmations.resolve_plan(
            prepared.plan_confirmation.confirmation_id,
            approved,
            prepared.plan,
            prepared.preview,
        )
        self._repository.record_confirmation(
            prepared.preview.transaction_id,
            confirmation_id=confirmation.confirmation_id,
            tier=confirmation.tier.value,
            state=confirmation.state.value,
            binding_digest=confirmation.preview_digest,
            confirmed_at=confirmation.confirmed_at,
        )
        target = (
            TransactionState.AWAITING_RUNTIME_CONFIRMATION
            if approved
            else TransactionState.CANCELLED
        )
        self._repository.transition(prepared.preview.transaction_id, target)
        self._audit.confirmation_resolved(prepared.plan, confirmation)
        return confirmation

    def request_runtime_confirmation(
        self,
        prepared: PreparedTrashOperation,
    ) -> RuntimeConfirmedTrashOperation:
        """Revalidate the live tree and issue the second short-lived confirmation."""
        transaction = self._repository.get_transaction(prepared.preview.transaction_id)
        if transaction.state is not TransactionState.AWAITING_RUNTIME_CONFIRMATION:
            raise OperationStoreError("Trash transaction is not awaiting runtime confirmation")
        current = self._preview.require_unchanged(prepared.plan, prepared.preview)
        confirmation = self._confirmations.request_runtime(
            prepared.plan_confirmation.confirmation_id,
            prepared.plan,
            current,
        )
        self._repository.record_confirmation(
            current.transaction_id,
            confirmation_id=confirmation.confirmation_id,
            tier=confirmation.tier.value,
            state=confirmation.state.value,
            binding_digest=confirmation.preview_digest,
            confirmed_at=None,
        )
        return RuntimeConfirmedTrashOperation(prepared, current, confirmation)

    def resolve_runtime_confirmation(
        self,
        runtime: RuntimeConfirmedTrashOperation,
        approved: bool,
    ) -> TrashConfirmation:
        """Persist the immediate decision and transition to confirmed only on approval."""
        confirmation = self._confirmations.resolve_runtime(
            runtime.runtime_confirmation.confirmation_id,
            approved,
            runtime.prepared.plan,
            runtime.runtime_preview,
        )
        self._repository.record_confirmation(
            runtime.runtime_preview.transaction_id,
            confirmation_id=confirmation.confirmation_id,
            tier=confirmation.tier.value,
            state=confirmation.state.value,
            binding_digest=confirmation.preview_digest,
            confirmed_at=confirmation.confirmed_at,
        )
        self._repository.transition(
            runtime.runtime_preview.transaction_id,
            TransactionState.CONFIRMED if approved else TransactionState.CANCELLED,
            confirmation_id=confirmation.confirmation_id,
            confirmed_at=confirmation.confirmed_at,
        )
        self._audit.confirmation_resolved(runtime.prepared.plan, confirmation)
        return confirmation

    def execute(
        self,
        runtime: RuntimeConfirmedTrashOperation,
        cancellation: CancellationToken | None = None,
    ) -> TrashExecutionReport:
        """Consume runtime confirmation, journal recovery first, and stop on ambiguity."""
        token = cancellation or CancellationToken()
        consumed = self._confirmations.consume_runtime(
            runtime.runtime_confirmation.confirmation_id,
            runtime.prepared.plan,
            runtime.runtime_preview,
        )
        self._repository.record_confirmation(
            runtime.runtime_preview.transaction_id,
            confirmation_id=consumed.confirmation_id,
            tier=consumed.tier.value,
            state=consumed.state.value,
            binding_digest=consumed.preview_digest,
            confirmed_at=consumed.confirmed_at,
        )
        transaction = self._repository.get_transaction(runtime.runtime_preview.transaction_id)
        if transaction.state is not TransactionState.CONFIRMED:
            raise OperationStoreError("Trash transaction is not durably confirmed")
        # Revalidate again after the second dialog and before changing durable state.
        current = self._preview.require_unchanged(runtime.prepared.plan, runtime.runtime_preview)
        transaction = self._repository.transition(
            transaction.transaction_id, TransactionState.RUNNING
        )
        preview_by_id = {item.operation_id: item for item in current.items}
        results: list[RecycleBinResult] = []
        failed = False
        ambiguous = False
        for item in runtime.prepared.plan.items:
            preview_item = preview_by_id[item.operation_id]
            if preview_item.status is not TrashPreviewStatus.READY or preview_item.snapshot is None:
                continue
            if token.is_cancelled:
                transaction = self._repository.transition(
                    transaction.transaction_id, TransactionState.CANCELLED
                )
                return self._report(runtime, transaction.transaction_id, results)
            payload = TrashRequest(
                source=item.source,
                expected_source_state=preview_item.snapshot.root_state,
                expected_snapshot=preview_item.snapshot,
            ).model_dump(mode="json")
            recovery = TrashRecoveryRecord(
                transaction_id=transaction.transaction_id,
                operation_id=item.operation_id,
                sequence=item.sequence,
                original_path=item.source,
                before_state=preview_item.snapshot.root_state,
            )
            self._repository.begin_trash_operation(
                transaction.transaction_id, item.operation_id, recovery
            )
            self._audit.item_started(
                runtime.prepared.plan,
                item,
                recovery,
                str(consumed.confirmation_id),
            )
            authorization = ExecutionAuthorization(
                transaction_id=transaction.transaction_id,
                operation_id=item.operation_id,
                plan_id=runtime.prepared.plan.plan_id,
                preview_id=runtime.prepared.preview.preview_id,
                tool_name="file.trash",
                arguments_digest=arguments_digest(payload),
                runtime_confirmation_id=consumed.confirmation_id,
            )
            result: RecycleBinResult | None = None
            error: Exception | None = None
            try:
                raw = self._registry.execute("file.trash", payload, token, authorization)
                if not isinstance(raw, TrashResult):
                    raise RuntimeError("Registered trash tool returned an invalid result")
                result = raw.outcome
                results.append(result)
                if (
                    result.recycled
                    and result.verification_status is RecycleVerificationStatus.VERIFIED_RECYCLED
                ):
                    status = RecoveryStatus.AVAILABLE
                elif result.verification_status is RecycleVerificationStatus.UNKNOWN:
                    status = RecoveryStatus.UNKNOWN
                    ambiguous = True
                else:
                    status = RecoveryStatus.FAILED
                recovery = recovery.model_copy(
                    update={
                        "status": status,
                        "recycled_at": datetime.now(UTC) if result.recycled else None,
                        "recycle_item_identifier": result.recycle_item_identifier,
                        "verification_status": result.verification_status,
                        "result_message": result.message,
                    }
                )
                self._repository.complete_trash_operation(item.operation_id, recovery)
                if status is not RecoveryStatus.AVAILABLE:
                    failed = True
            except Exception as exc:
                error = exc
                failed = True
                recovery = recovery.model_copy(
                    update={
                        "status": RecoveryStatus.FAILED,
                        "verification_status": RecycleVerificationStatus.FAILED,
                        "result_message": str(exc),
                    }
                )
                with suppress(OperationStoreError):
                    self._repository.complete_trash_operation(item.operation_id, recovery)
            self._audit.item_result(runtime.prepared.plan, item, result, recovery, error)
            if failed:
                break
        transaction = self._repository.get_transaction(transaction.transaction_id)
        terminal = (
            TransactionState.PARTIALLY_COMPLETED
            if failed and transaction.completed_count
            else TransactionState.UNKNOWN
            if ambiguous
            else TransactionState.FAILED
            if failed
            else TransactionState.COMPLETED
        )
        self._repository.transition(transaction.transaction_id, terminal)
        return self._report(runtime, transaction.transaction_id, results)

    def _report(
        self,
        runtime: RuntimeConfirmedTrashOperation,
        transaction_id: UUID,
        results: list[RecycleBinResult],
    ) -> TrashExecutionReport:
        transaction = self._repository.get_transaction(transaction_id)
        return TrashExecutionReport(
            transaction_id=transaction_id,
            plan_id=runtime.prepared.plan.plan_id,
            completed_count=transaction.completed_count,
            failed_count=transaction.failed_count,
            skipped_count=transaction.skipped_count,
            total_size_bytes=runtime.runtime_preview.total_size_bytes,
            results=tuple(results),
        )

    def recovery_records(self, transaction_id: UUID) -> tuple[TrashRecoveryRecord, ...]:
        """Return integrity-checked MANUAL recovery guidance for transaction history."""
        return self._repository.list_recovery(transaction_id)
