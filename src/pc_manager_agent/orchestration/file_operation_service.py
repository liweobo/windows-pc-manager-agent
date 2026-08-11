"""Application service coordinating safety review, Preview, confirmation, and execution."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pc_manager_agent.audit.file_operations import OperationAuditLogger
from pc_manager_agent.confirmation.file_operations import (
    OperationConfirmation,
    OperationConfirmationService,
)
from pc_manager_agent.domain.file_operations import FileOperationPlan, FileOperationPreview
from pc_manager_agent.domain.transactions import (
    OperationExecutionReport,
    OperationTransaction,
    TransactionState,
)
from pc_manager_agent.orchestration.transaction_executor import (
    TransactionExecutor,
    build_all_operation_arguments,
)
from pc_manager_agent.persistence.file_operations import OperationRepository
from pc_manager_agent.safety.file_operation_validator import FileOperationSafetyValidator
from pc_manager_agent.safety.operation_preview import OperationPreviewEngine
from pc_manager_agent.safety.plan_reviewer import SafetyReview
from pc_manager_agent.tools.manifest import CancellationToken


@dataclass(frozen=True, slots=True)
class PreparedFileOperation:
    """Reviewed Preview and pending confirmation returned to UI or headless callers."""

    plan: FileOperationPlan
    review: SafetyReview
    preview: FileOperationPreview
    confirmation: OperationConfirmation


class FileOperationService:
    """Keep the UI outside every deterministic Stage 2A security boundary."""

    def __init__(
        self,
        validator: FileOperationSafetyValidator,
        preview_engine: OperationPreviewEngine,
        confirmations: OperationConfirmationService,
        repository: OperationRepository,
        executor: TransactionExecutor,
        audit: OperationAuditLogger,
    ) -> None:
        self._validator = validator
        self._preview_engine = preview_engine
        self._confirmations = confirmations
        self._repository = repository
        self._executor = executor
        self._audit = audit

    def prepare(self, plan: FileOperationPlan) -> PreparedFileOperation:
        """Review, Preview, persist, and request confirmation without mutating files."""
        review = self._validator.review(plan)
        if not review.approved:
            details = "; ".join(issue.message for issue in review.issues)
            raise PermissionError(f"File-operation safety review denied the plan: {details}")
        preview = self._preview_engine.generate(plan)
        if preview.ready_count == 0:
            raise PermissionError("Preview contains no safe executable operation")
        arguments = build_all_operation_arguments(plan, preview)
        self._repository.create_from_preview(plan, preview, arguments)
        self._repository.transition(preview.transaction_id, TransactionState.AWAITING_CONFIRMATION)
        confirmation = self._confirmations.request(plan, preview)
        self._audit.previewed(plan, preview)
        return PreparedFileOperation(
            plan=plan,
            review=review,
            preview=preview,
            confirmation=confirmation,
        )

    def resolve_confirmation(
        self,
        prepared: PreparedFileOperation,
        approved: bool,
    ) -> OperationConfirmation:
        """Persist approval or cancellation for the exact current Preview."""
        confirmation = self._confirmations.resolve(
            prepared.confirmation.confirmation_id,
            approved,
            prepared.plan,
            prepared.preview,
        )
        state = TransactionState.CONFIRMED if approved else TransactionState.CANCELLED
        self._repository.transition(
            prepared.preview.transaction_id,
            state,
            confirmation_id=confirmation.confirmation_id,
            confirmed_at=confirmation.confirmed_at,
        )
        self._audit.confirmation_resolved(prepared.plan, confirmation)
        return confirmation

    def execute(
        self,
        prepared: PreparedFileOperation,
        cancellation: CancellationToken | None = None,
    ) -> OperationExecutionReport:
        """Execute only after the exact prepared confirmation has been approved."""
        return self._executor.execute(
            prepared.plan,
            prepared.preview,
            prepared.confirmation.confirmation_id,
            cancellation,
        )

    def get_transaction(self, transaction_id: UUID) -> OperationTransaction:
        """Return one operation transaction for history and diagnostics."""
        return self._repository.get_transaction(transaction_id)
