"""Stage 4D4 workflow for assessment, confirmation, recycling, and verification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pc_manager_agent.audit.residual_cleanup import ResidualCleanupAuditLogger
from pc_manager_agent.confirmation.residual_cleanup import (
    ResidualCleanupConfirmation,
    ResidualCleanupConfirmationError,
    ResidualCleanupConfirmationService,
)
from pc_manager_agent.domain.residual_cleanup import (
    PlannedResidualCleanupItem,
    ResidualCleanupAssessment,
    ResidualCleanupExecutionReport,
    ResidualCleanupItemResult,
    ResidualCleanupItemState,
    ResidualCleanupPlan,
    ResidualCleanupPreview,
    ResidualCleanupRequest,
    ResidualCleanupTransactionState,
    ResidualCleanupTrashResult,
    ResidualVerificationStatus,
)
from pc_manager_agent.domain.trash import RecycleVerificationStatus
from pc_manager_agent.persistence.residual_cleanup import (
    ResidualCleanupRepository,
    ResidualCleanupStoreError,
)
from pc_manager_agent.platform_support.base import FileOperationPlatform
from pc_manager_agent.recovery.models import RecoveryStatus, TrashRecoveryRecord
from pc_manager_agent.safety.plan_reviewer import SafetyReview
from pc_manager_agent.safety.residual_cleanup_preview import ResidualCleanupPreviewEngine
from pc_manager_agent.safety.residual_cleanup_validator import (
    ResidualCleanupSafetyValidator,
)
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class PreparedResidualCleanup:
    """Reviewed plan, fresh Preview, and pending first confirmation returned to UI."""

    request: ResidualCleanupRequest
    assessment: ResidualCleanupAssessment
    plan: ResidualCleanupPlan
    review: SafetyReview
    preview: ResidualCleanupPreview
    plan_confirmation: ResidualCleanupConfirmation


@dataclass(frozen=True, slots=True)
class RuntimeResidualCleanup:
    """Separately rescanned Preview and pending immediate confirmation."""

    prepared: PreparedResidualCleanup
    preview: ResidualCleanupPreview
    confirmation: ResidualCleanupConfirmation


class ResidualCleanupService:
    """Keep UI, providers, paths, and Windows mutation behind deterministic gates."""

    def __init__(
        self,
        registry: ToolRegistry,
        preview_engine: ResidualCleanupPreviewEngine,
        validator: ResidualCleanupSafetyValidator,
        confirmations: ResidualCleanupConfirmationService,
        repository: ResidualCleanupRepository,
        identity_platform: FileOperationPlatform,
        audit: ResidualCleanupAuditLogger,
    ) -> None:
        self._registry = registry
        self._preview = preview_engine
        self._validator = validator
        self._confirmations = confirmations
        self._repository = repository
        self._identity = identity_platform
        self._audit = audit

    def assess(
        self,
        request: ResidualCleanupRequest,
        cancellation: CancellationToken | None = None,
    ) -> ResidualCleanupAssessment:
        """Run the registered R0 fresh-assessment tool and audit its exact selection."""
        result = self._registry.execute(
            "software.residuals.prepare_cleanup",
            request.model_dump(mode="json"),
            cancellation,
        )
        if not isinstance(result, ResidualCleanupAssessment):
            raise RuntimeError("Residual cleanup preparation returned an invalid assessment")
        self._audit.assessed(result)
        return result

    def prepare(self, assessment: ResidualCleanupAssessment) -> PreparedResidualCleanup:
        """Compile an all-eligible plan, persist it, and request plan confirmation."""
        plan, preview = self._preview.compile(assessment)
        review = self._validator.review(plan)
        if not review.approved:
            messages = "; ".join(issue.message for issue in review.issues)
            raise PermissionError(f"Residual cleanup safety review denied the plan: {messages}")
        self._repository.create(plan, preview)
        confirmation = self._confirmations.request_plan(plan, preview)
        self._audit.previewed(plan, preview)
        return PreparedResidualCleanup(
            request=assessment.request,
            assessment=assessment,
            plan=plan,
            review=review,
            preview=preview,
            plan_confirmation=confirmation,
        )

    def resolve_plan_confirmation(
        self,
        prepared: PreparedResidualCleanup,
        approved: bool,
    ) -> ResidualCleanupConfirmation:
        """Persist the first decision; approval alone still cannot dispatch a tool."""
        confirmation = self._confirmations.resolve(
            prepared.plan_confirmation.confirmation_id,
            approved,
            prepared.plan,
            prepared.preview,
        )
        self._audit.confirmation_resolved(prepared.plan, confirmation)
        return confirmation

    def request_runtime_confirmation(
        self,
        prepared: PreparedResidualCleanup,
        cancellation: CancellationToken | None = None,
    ) -> RuntimeResidualCleanup:
        """Repeat the exact scan, bind the new Preview, and request immediate approval."""
        if (
            self._repository.state(prepared.plan.transaction_id)
            is not ResidualCleanupTransactionState.PLAN_CONFIRMED
        ):
            raise ResidualCleanupStoreError("Residual cleanup transaction is not plan-confirmed")
        try:
            runtime_preview = self._preview.revalidate(
                prepared.plan,
                prepared.request,
                cancellation,
            )
        except Exception as exc:
            self._repository.transition(
                prepared.plan.transaction_id,
                ResidualCleanupTransactionState.BLOCKED,
                error_message="Runtime Fresh Revalidation failed",
            )
            self._audit.workflow_blocked(
                prepared.plan,
                phase="runtime-fresh-revalidation",
                error_type=type(exc).__name__,
            )
            raise
        self._repository.bind_runtime_preview(prepared.plan, runtime_preview)
        confirmation = self._confirmations.request_runtime(
            prepared.plan_confirmation.confirmation_id,
            prepared.plan,
            runtime_preview,
        )
        return RuntimeResidualCleanup(prepared, runtime_preview, confirmation)

    def resolve_runtime_confirmation(
        self,
        runtime: RuntimeResidualCleanup,
        approved: bool,
    ) -> ResidualCleanupConfirmation:
        """Persist the object-specific immediate decision."""
        confirmation = self._confirmations.resolve(
            runtime.confirmation.confirmation_id,
            approved,
            runtime.prepared.plan,
            runtime.preview,
        )
        self._audit.confirmation_resolved(runtime.prepared.plan, confirmation)
        return confirmation

    def execute(
        self,
        runtime: RuntimeResidualCleanup,
        cancellation: CancellationToken | None = None,
    ) -> ResidualCleanupExecutionReport:
        """Consume approvals, execute sequentially, stop on failure, and verify identity."""
        token = cancellation or CancellationToken()
        plan = runtime.prepared.plan
        if (
            self._repository.state(plan.transaction_id)
            is not ResidualCleanupTransactionState.AWAITING_RUNTIME_CONFIRMATION
        ):
            raise ResidualCleanupConfirmationError(
                "Residual cleanup immediate confirmation is absent, stale, or already used"
            )
        if token.cancellation_requested():
            self._repository.transition(
                plan.transaction_id,
                ResidualCleanupTransactionState.CANCELLED,
                error_message="Cancelled before final Fresh Revalidation",
            )
            raise ResidualCleanupConfirmationError(
                "Residual cleanup was cancelled before any Recycle Bin operation"
            )
        # A third whole-batch scan closes the confirmation-to-dispatch gap. Its
        # transient Preview ID is deliberately not used as new authorization.
        try:
            self._preview.revalidate(plan, runtime.prepared.request, token)
        except Exception as exc:
            if token.cancellation_requested():
                self._repository.transition(
                    plan.transaction_id,
                    ResidualCleanupTransactionState.CANCELLED,
                    error_message="Cancelled during final Fresh Revalidation",
                )
            else:
                self._repository.transition(
                    plan.transaction_id,
                    ResidualCleanupTransactionState.BLOCKED,
                    error_message="Final TOCTOU Fresh Revalidation failed",
                )
                self._audit.workflow_blocked(
                    plan,
                    phase="final-toctou-revalidation",
                    error_type=type(exc).__name__,
                )
            raise
        consumed = self._confirmations.consume_runtime(
            runtime.confirmation.confirmation_id,
            plan,
            runtime.preview,
        )
        failure = False
        cancellation_seen = False
        for item in plan.items:
            if token.cancellation_requested():
                cancellation_seen = True
                break
            self._repository.begin_item_validation(plan.transaction_id, item.item_ref)
            candidate = item.candidate
            if candidate.fresh_identity is None:
                failure = True
                self._complete_pre_dispatch_failure(
                    plan,
                    item,
                    "Fresh residual identity is unavailable",
                )
                break
            recovery = TrashRecoveryRecord(
                transaction_id=plan.transaction_id,
                operation_id=item.operation_id,
                sequence=item.sequence,
                original_path=candidate.path,
                before_state=candidate.fresh_identity,
            )
            self._repository.prepare_recovery(item.item_ref, recovery)
            # Mandatory audit storage is checked before registry dispatch.
            self._audit.item_started(
                plan,
                item,
                recovery,
                str(consumed.confirmation_id),
            )
            request = self._repository.request_for_item(
                plan,
                runtime.preview.preview_id,
                item,
            )
            payload = request.model_dump(mode="json")
            authorization = ExecutionAuthorization(
                transaction_id=plan.transaction_id,
                operation_id=item.operation_id,
                plan_id=plan.plan_id,
                preview_id=runtime.preview.preview_id,
                tool_name=item.tool_name,
                arguments_digest=arguments_digest(payload),
                runtime_confirmation_id=consumed.confirmation_id,
            )
            try:
                raw = self._registry.execute(
                    item.tool_name,
                    payload,
                    token,
                    authorization,
                )
                if not isinstance(raw, ResidualCleanupTrashResult):
                    raise RuntimeError("Residual cleanup tool returned an invalid result")
                self._repository.mark_item_verifying(item.item_ref)
                result, recovery = self._verify_result(item, raw, recovery)
            except Exception as exc:
                failure = True
                recovery = recovery.model_copy(
                    update={
                        "status": RecoveryStatus.FAILED,
                        "verification_status": RecycleVerificationStatus.FAILED,
                        "result_message": str(exc),
                    }
                )
                result = ResidualCleanupItemResult(
                    item_ref=item.item_ref,
                    operation_id=item.operation_id,
                    sequence=item.sequence,
                    source_candidate_id=candidate.source_candidate_id,
                    path=candidate.path,
                    state=ResidualCleanupItemState.BLOCKED_CHANGED,
                    verification_status=ResidualVerificationStatus.UNKNOWN,
                    recovery_id=recovery.recovery_id,
                    message=f"Cleanup stopped safely: {type(exc).__name__}: {exc}",
                )
            self._repository.complete_item(result, recovery)
            self._audit.item_completed(plan, result, recovery)
            if result.state is not ResidualCleanupItemState.VERIFIED:
                failure = True
                break
        if failure or cancellation_seen:
            reason = (
                "Skipped because the user stopped future cleanup operations"
                if cancellation_seen
                else "Skipped because an earlier cleanup item failed or changed"
            )
            self._repository.skip_pending(plan.transaction_id, reason)
        report = self._finalize(plan, failure, cancellation_seen)
        self._audit.transaction_completed(report)
        return report

    def recovery_records(self, transaction_id: UUID) -> tuple[TrashRecoveryRecord, ...]:
        """Return integrity-checked MANUAL recovery instructions for completed items."""
        return tuple(
            record
            for record in self._repository.list_recovery(transaction_id)
            if record.status is RecoveryStatus.AVAILABLE
            and record.recycle_item_identifier is not None
        )

    def _verify_result(
        self,
        item: PlannedResidualCleanupItem,
        raw: ResidualCleanupTrashResult,
        recovery: TrashRecoveryRecord,
    ) -> tuple[ResidualCleanupItemResult, TrashRecoveryRecord]:
        outcome = raw.outcome
        original = raw.original_identity
        current = None
        inspection_failed = False
        try:
            current = self._identity.inspect(original.path)
        except FileNotFoundError:
            current = None
        except OSError:
            inspection_failed = True
        shell_evidence = (
            not outcome.aborted
            and outcome.recycled
            and outcome.verification_status is RecycleVerificationStatus.VERIFIED_RECYCLED
            and outcome.recycle_item_identifier is not None
            and outcome.hresult & 0x80000000 == 0
        )
        if inspection_failed:
            verification = ResidualVerificationStatus.UNKNOWN
            verified = False
            message = "Original-path identity could not be inspected after the Shell operation"
        elif current is not None and current.identity_matches(original):
            verification = ResidualVerificationStatus.ORIGINAL_IDENTITY_STILL_PRESENT
            verified = False
            message = "Windows returned but the original filesystem identity is still present"
        elif current is not None:
            verification = ResidualVerificationStatus.ORIGINAL_REMOVED_NEW_OBJECT_PRESENT
            verified = shell_evidence
            message = (
                "Original residual identity was removed; a different object now occupies the path"
            )
        else:
            verification = ResidualVerificationStatus.ORIGINAL_IDENTITY_REMOVED
            verified = shell_evidence
            message = "Original residual identity is no longer present at its original path"
        if not shell_evidence:
            verification = ResidualVerificationStatus.UNKNOWN
            verified = False
            message = "Windows did not provide complete Recycle Bin and identity evidence"
        recovery = recovery.model_copy(
            update={
                "status": RecoveryStatus.AVAILABLE if verified else RecoveryStatus.UNKNOWN,
                "recycled_at": datetime.now(UTC) if verified else None,
                "recycle_item_identifier": outcome.recycle_item_identifier,
                "verification_status": (
                    RecycleVerificationStatus.VERIFIED_RECYCLED
                    if verified
                    else RecycleVerificationStatus.UNKNOWN
                ),
                "result_message": message,
            }
        )
        result = ResidualCleanupItemResult(
            item_ref=item.item_ref,
            operation_id=item.operation_id,
            sequence=item.sequence,
            source_candidate_id=item.candidate.source_candidate_id,
            path=item.candidate.path,
            state=(
                ResidualCleanupItemState.VERIFIED if verified else ResidualCleanupItemState.FAILED
            ),
            verification_status=verification,
            recycle_result=outcome,
            recovery_id=recovery.recovery_id,
            message=message,
        )
        return result, recovery

    def _complete_pre_dispatch_failure(
        self,
        plan: ResidualCleanupPlan,
        item: PlannedResidualCleanupItem,
        message: str,
    ) -> None:
        result = ResidualCleanupItemResult(
            item_ref=item.item_ref,
            operation_id=item.operation_id,
            sequence=item.sequence,
            source_candidate_id=item.candidate.source_candidate_id,
            path=item.candidate.path,
            state=ResidualCleanupItemState.BLOCKED_CHANGED,
            verification_status=ResidualVerificationStatus.UNKNOWN,
            message=message,
        )
        self._repository.complete_item(result, None)
        self._audit.item_completed(plan, result, None)

    def _finalize(
        self,
        plan: ResidualCleanupPlan,
        failure: bool,
        cancellation_seen: bool,
    ) -> ResidualCleanupExecutionReport:
        results = self._repository.list_results(plan.transaction_id)
        completed = sum(item.state is ResidualCleanupItemState.VERIFIED for item in results)
        failed = sum(
            item.state
            in {
                ResidualCleanupItemState.FAILED,
                ResidualCleanupItemState.BLOCKED_CHANGED,
            }
            for item in results
        )
        skipped = sum(item.state is ResidualCleanupItemState.SKIPPED for item in results)
        if completed == plan.total_items:
            state = ResidualCleanupTransactionState.COMPLETED
        elif completed:
            state = ResidualCleanupTransactionState.PARTIALLY_COMPLETED
        elif cancellation_seen and not failure:
            state = ResidualCleanupTransactionState.CANCELLED
        else:
            state = ResidualCleanupTransactionState.FAILED
        self._repository.transition(plan.transaction_id, state)
        return ResidualCleanupExecutionReport(
            transaction_id=plan.transaction_id,
            plan_id=plan.plan_id,
            final_state=state,
            completed_count=completed,
            failed_count=failed,
            skipped_count=skipped,
            total_size_bytes=plan.total_bytes,
            results=results,
        )
