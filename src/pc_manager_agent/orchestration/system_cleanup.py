"""Stage 4E2 orchestration for controlled item cleanup and independent Bin emptying."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pc_manager_agent.audit.system_cleanup import SystemCleanupAuditLogger
from pc_manager_agent.confirmation.system_cleanup import (
    SystemCleanupConfirmation,
    SystemCleanupConfirmationError,
    SystemCleanupConfirmationService,
)
from pc_manager_agent.domain.system_cleanup_execution import (
    CleanupExecutionPlan,
    CleanupExecutionPreview,
    CleanupIrreversibilityRecord,
    CleanupItemResult,
    CleanupItemState,
    CleanupResult,
    CleanupTransactionState,
    CleanupVerificationStatus,
    PlannedCleanupItem,
    RecycleBinEmptyPlan,
    RecycleBinEmptyPreview,
    RecycleBinEmptyResult,
    SystemCleanupAssessment,
    SystemCleanupRequest,
    SystemCleanupTrashResult,
)
from pc_manager_agent.domain.trash import RecycleVerificationStatus
from pc_manager_agent.persistence.system_cleanup import (
    SystemCleanupRepository,
    SystemCleanupStoreError,
)
from pc_manager_agent.platform_support.base import FileOperationPlatform
from pc_manager_agent.recovery.models import RecoveryStatus, TrashRecoveryRecord
from pc_manager_agent.safety.plan_reviewer import SafetyReview
from pc_manager_agent.safety.recycle_bin_empty import RecycleBinEmptyPlanBuilder
from pc_manager_agent.safety.system_cleanup_preview import CleanupExecutionPlanBuilder
from pc_manager_agent.safety.system_cleanup_validator import SystemCleanupSafetyValidator
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class PreparedSystemCleanup:
    """Reviewed item plan, Fresh Preview, and pending first confirmation."""

    request: SystemCleanupRequest
    assessment: SystemCleanupAssessment
    plan: CleanupExecutionPlan
    review: SafetyReview
    preview: CleanupExecutionPreview
    plan_confirmation: SystemCleanupConfirmation


@dataclass(frozen=True, slots=True)
class RuntimeSystemCleanup:
    """Separately revalidated Preview and pending immediate confirmation."""

    prepared: PreparedSystemCleanup
    preview: CleanupExecutionPreview
    confirmation: SystemCleanupConfirmation


@dataclass(frozen=True, slots=True)
class PreparedRecycleBinEmpty:
    """Independent irreversible plan and pending first confirmation."""

    plan: RecycleBinEmptyPlan
    preview: RecycleBinEmptyPreview
    plan_confirmation: SystemCleanupConfirmation


@dataclass(frozen=True, slots=True)
class RuntimeRecycleBinEmpty:
    """Fresh exact-volume Preview and pending immediate confirmation."""

    prepared: PreparedRecycleBinEmpty
    preview: RecycleBinEmptyPreview
    confirmation: SystemCleanupConfirmation


class SystemCleanupService:
    """Keep UI, models, paths, and Windows mutation behind deterministic gates."""

    def __init__(
        self,
        registry: ToolRegistry,
        plans: CleanupExecutionPlanBuilder,
        empty_plans: RecycleBinEmptyPlanBuilder,
        validator: SystemCleanupSafetyValidator,
        confirmations: SystemCleanupConfirmationService,
        repository: SystemCleanupRepository,
        identity_platform: FileOperationPlatform,
        audit: SystemCleanupAuditLogger,
    ) -> None:
        self._registry = registry
        self._plans = plans
        self._empty_plans = empty_plans
        self._validator = validator
        self._confirmations = confirmations
        self._repository = repository
        self._identity = identity_platform
        self._audit = audit

    def assess(
        self,
        request: SystemCleanupRequest,
        cancellation: CancellationToken | None = None,
    ) -> SystemCleanupAssessment:
        """Run registered R0 Fresh discovery and audit its non-authoritative results."""
        result = self._registry.execute(
            "optimization.cleanup.prepare",
            request.model_dump(mode="json"),
            cancellation,
        )
        if not isinstance(result, SystemCleanupAssessment):
            raise RuntimeError("System cleanup preparation returned an invalid assessment")
        self._audit.assessed(result)
        return result

    def prepare(
        self,
        assessment: SystemCleanupAssessment,
        selected_item_refs: tuple[UUID, ...],
    ) -> PreparedSystemCleanup:
        """Compile the exact second selection, persist it, and request plan approval."""
        plan, preview = self._plans.compile(assessment, selected_item_refs)
        review = self._validator.review(plan)
        if not review.approved:
            messages = "; ".join(issue.message for issue in review.issues)
            raise PermissionError(f"System cleanup safety review denied the plan: {messages}")
        self._repository.create_item_cleanup(plan, preview)
        confirmation = self._confirmations.request_plan(plan, preview)
        self._audit.previewed(plan, preview)
        return PreparedSystemCleanup(
            request=assessment.request,
            assessment=assessment,
            plan=plan,
            review=review,
            preview=preview,
            plan_confirmation=confirmation,
        )

    def resolve_plan_confirmation(
        self,
        prepared: PreparedSystemCleanup,
        approved: bool,
    ) -> SystemCleanupConfirmation:
        """Persist the first decision; approval alone cannot dispatch a writer."""
        confirmation = self._confirmations.resolve(
            prepared.plan_confirmation.confirmation_id,
            approved,
            prepared.plan,
            prepared.preview,
        )
        self._audit.confirmation_resolved(confirmation)
        return confirmation

    def request_runtime_confirmation(
        self,
        prepared: PreparedSystemCleanup,
        cancellation: CancellationToken | None = None,
    ) -> RuntimeSystemCleanup:
        """Revalidate exact selected items and request the immediate approval."""
        if self._repository.state(prepared.plan.transaction_id) is not (
            CleanupTransactionState.PLAN_CONFIRMED
        ):
            raise SystemCleanupStoreError("System cleanup transaction is not plan-confirmed")
        try:
            preview = self._plans.revalidate(prepared.plan, cancellation)
        except Exception as exc:
            self._block(prepared.plan, "runtime-fresh-revalidation", exc)
            raise
        self._repository.bind_runtime_preview(prepared.plan, preview)
        confirmation = self._confirmations.request_runtime(
            prepared.plan_confirmation.confirmation_id,
            prepared.plan,
            preview,
        )
        return RuntimeSystemCleanup(prepared, preview, confirmation)

    def resolve_runtime_confirmation(
        self,
        runtime: RuntimeSystemCleanup,
        approved: bool,
    ) -> SystemCleanupConfirmation:
        """Persist the immediate object-specific decision."""
        confirmation = self._confirmations.resolve(
            runtime.confirmation.confirmation_id,
            approved,
            runtime.prepared.plan,
            runtime.preview,
        )
        self._audit.confirmation_resolved(confirmation)
        return confirmation

    def execute(
        self,
        runtime: RuntimeSystemCleanup,
        cancellation: CancellationToken | None = None,
    ) -> CleanupResult:
        """Consume approvals, execute sequentially, stop future work, and verify identity."""
        token = cancellation or CancellationToken()
        plan = runtime.prepared.plan
        if self._repository.state(plan.transaction_id) is not (
            CleanupTransactionState.AWAITING_RUNTIME_CONFIRMATION
        ):
            raise SystemCleanupConfirmationError(
                "Cleanup immediate confirmation is absent, stale, or already used"
            )
        if token.cancellation_requested():
            self._repository.transition(plan.transaction_id, CleanupTransactionState.CANCELLED)
            raise SystemCleanupConfirmationError("Cleanup cancelled before any Shell operation")
        try:
            self._plans.revalidate(plan, token)
        except Exception as exc:
            self._block(plan, "final-batch-toctou-revalidation", exc)
            raise
        consumed = self._confirmations.consume_runtime(
            runtime.confirmation.confirmation_id,
            plan,
            runtime.preview,
        )
        failure = cancellation_seen = False
        for item in plan.items:
            if token.cancellation_requested():
                cancellation_seen = True
                break
            self._repository.begin_item_validation(
                plan.transaction_id,
                item.candidate.item_ref,
            )
            candidate = item.candidate
            if candidate.fresh_identity is None or candidate.path is None:
                failure = True
                self._pre_dispatch_failure(plan, item, "Fresh identity is unavailable")
                break
            recovery = TrashRecoveryRecord(
                transaction_id=plan.transaction_id,
                operation_id=item.operation_id,
                sequence=item.sequence,
                original_path=candidate.path,
                before_state=candidate.fresh_identity.state,
            )
            self._repository.prepare_recovery(candidate.item_ref, recovery)
            self._audit.item_started(plan, item, recovery, str(consumed.confirmation_id))
            request = self._repository.request_for_item(plan, runtime.preview.preview_id, item)
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
                if not isinstance(raw, SystemCleanupTrashResult):
                    raise RuntimeError("Cleanup tool returned an invalid result")
                self._repository.mark_item_verifying(candidate.item_ref)
                result, recovery = self._verify_item(item, raw, recovery)
            except Exception as exc:
                failure = True
                recovery = recovery.model_copy(
                    update={
                        "status": RecoveryStatus.FAILED,
                        "verification_status": RecycleVerificationStatus.FAILED,
                        "result_message": str(exc),
                    }
                )
                result = CleanupItemResult(
                    item_ref=candidate.item_ref,
                    operation_id=item.operation_id,
                    sequence=item.sequence,
                    source_candidate_id=candidate.source_candidate_id,
                    path=candidate.path,
                    state=CleanupItemState.BLOCKED_CHANGED,
                    verification_status=CleanupVerificationStatus.UNKNOWN,
                    recovery_id=recovery.recovery_id,
                    message=f"Cleanup stopped safely: {type(exc).__name__}: {exc}",
                )
            self._repository.complete_item(result, recovery)
            self._audit.item_completed(plan, result, recovery)
            if result.state is not CleanupItemState.VERIFIED:
                failure = True
                break
        if failure or cancellation_seen:
            self._repository.skip_pending(
                plan.transaction_id,
                "Skipped because future cleanup was stopped",
            )
        final_result = self._finalize(plan, failure, cancellation_seen)
        self._audit.transaction_completed(final_result)
        return final_result

    def prepare_recycle_bin_empty(self) -> PreparedRecycleBinEmpty:
        """Create a separate exact-volume plan; it cannot join an item batch."""
        plan, preview = self._empty_plans.prepare()
        self._repository.create_empty(plan, preview)
        confirmation = self._confirmations.request_plan(plan, preview)
        self._audit.empty_previewed(plan, preview)
        return PreparedRecycleBinEmpty(plan, preview, confirmation)

    def resolve_empty_plan_confirmation(
        self,
        prepared: PreparedRecycleBinEmpty,
        approved: bool,
    ) -> SystemCleanupConfirmation:
        """Persist the first independent irreversible-action decision."""
        confirmation = self._confirmations.resolve(
            prepared.plan_confirmation.confirmation_id,
            approved,
            prepared.plan,
            prepared.preview,
        )
        self._audit.confirmation_resolved(confirmation)
        return confirmation

    def request_empty_runtime_confirmation(
        self,
        prepared: PreparedRecycleBinEmpty,
    ) -> RuntimeRecycleBinEmpty:
        """Reinspect exact contents before requesting immediate irreversible approval."""
        if self._repository.state(prepared.plan.transaction_id) is not (
            CleanupTransactionState.PLAN_CONFIRMED
        ):
            raise SystemCleanupStoreError("Recycle Bin empty plan is not confirmed")
        try:
            preview = self._empty_plans.revalidate(prepared.plan)
        except Exception as exc:
            self._repository.transition(
                prepared.plan.transaction_id,
                CleanupTransactionState.BLOCKED,
                error_message="Recycle Bin Fresh revalidation failed",
            )
            self._audit.blocked(
                transaction_id=str(prepared.plan.transaction_id),
                plan_id=str(prepared.plan.plan_id),
                phase="recycle-bin-runtime-revalidation",
                error=type(exc).__name__,
            )
            raise
        self._repository.bind_empty_runtime_preview(prepared.plan, preview)
        confirmation = self._confirmations.request_runtime(
            prepared.plan_confirmation.confirmation_id,
            prepared.plan,
            preview,
        )
        return RuntimeRecycleBinEmpty(prepared, preview, confirmation)

    def resolve_empty_runtime_confirmation(
        self,
        runtime: RuntimeRecycleBinEmpty,
        approved: bool,
    ) -> SystemCleanupConfirmation:
        """Persist the immediate irreversible-action decision."""
        confirmation = self._confirmations.resolve(
            runtime.confirmation.confirmation_id,
            approved,
            runtime.prepared.plan,
            runtime.preview,
        )
        self._audit.confirmation_resolved(confirmation)
        return confirmation

    def execute_recycle_bin_empty(
        self,
        runtime: RuntimeRecycleBinEmpty,
        cancellation: CancellationToken | None = None,
    ) -> RecycleBinEmptyResult:
        """Consume separate approvals and perform one exact-volume Shell call."""
        token = cancellation or CancellationToken()
        plan = runtime.prepared.plan
        if token.cancellation_requested():
            self._repository.transition(plan.transaction_id, CleanupTransactionState.CANCELLED)
            raise SystemCleanupConfirmationError("Recycle Bin emptying cancelled")
        self._empty_plans.revalidate(plan)
        consumed = self._confirmations.consume_runtime(
            runtime.confirmation.confirmation_id,
            plan,
            runtime.preview,
        )
        self._repository.begin_empty_validation(plan.transaction_id)
        self._audit.empty_started(plan, str(consumed.confirmation_id))
        request = self._repository.request_for_empty(plan, runtime.preview.preview_id)
        payload = request.model_dump(mode="json")
        authorization = ExecutionAuthorization(
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_id=plan.plan_id,
            preview_id=runtime.preview.preview_id,
            tool_name="optimization.recycle_bin.empty",
            arguments_digest=arguments_digest(payload),
            runtime_confirmation_id=consumed.confirmation_id,
        )
        try:
            raw = self._registry.execute(
                "optimization.recycle_bin.empty",
                payload,
                token,
                authorization,
            )
            if not isinstance(raw, RecycleBinEmptyResult):
                raise RuntimeError("Recycle Bin empty tool returned an invalid result")
        except Exception as exc:
            self._repository.transition(
                plan.transaction_id,
                CleanupTransactionState.FAILED,
                error_message=(
                    "Recycle Bin emptying failed or its final verification was unavailable"
                ),
            )
            self._audit.blocked(
                transaction_id=str(plan.transaction_id),
                plan_id=str(plan.plan_id),
                phase="recycle-bin-empty-or-verification",
                error=type(exc).__name__,
            )
            raise
        record = CleanupIrreversibilityRecord(
            transaction_id=plan.transaction_id,
            volume_root=plan.snapshot.volume_root,
            item_count=plan.snapshot.item_count,
            observed_size_bytes=plan.snapshot.observed_size_bytes,
        )
        self._repository.complete_empty(raw, record)
        state = (
            CleanupTransactionState.COMPLETED
            if raw.verification_status is CleanupVerificationStatus.RECYCLE_BIN_EMPTY_VERIFIED
            else CleanupTransactionState.FAILED
        )
        self._repository.transition(plan.transaction_id, state)
        self._audit.empty_completed(raw)
        return raw

    def recovery_records(self, transaction_id: UUID) -> tuple[TrashRecoveryRecord, ...]:
        """Return only verified MANUAL recovery instructions for ordinary item cleanup."""
        return tuple(
            record
            for record in self._repository.list_recovery(transaction_id)
            if record.status is RecoveryStatus.AVAILABLE
            and record.recycle_item_identifier is not None
        )

    def _verify_item(
        self,
        item: PlannedCleanupItem,
        raw: SystemCleanupTrashResult,
        recovery: TrashRecoveryRecord,
    ) -> tuple[CleanupItemResult, TrashRecoveryRecord]:
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
            verification = CleanupVerificationStatus.UNKNOWN
            verified = False
            message = "Original identity could not be inspected after the Shell operation"
        elif current is not None and current.identity_matches(original):
            verification = CleanupVerificationStatus.ORIGINAL_OBJECT_STILL_PRESENT
            verified = False
            message = "The original filesystem identity is still present"
        elif current is not None:
            verification = CleanupVerificationStatus.ORIGINAL_REMOVED_NEW_OBJECT_PRESENT
            verified = shell_evidence
            message = "Original identity was removed; a different object now occupies the path"
        else:
            verification = CleanupVerificationStatus.ORIGINAL_OBJECT_REMOVED
            verified = shell_evidence
            message = "Original identity is no longer present at its original path"
        if not shell_evidence:
            verification = CleanupVerificationStatus.UNKNOWN
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
        candidate = item.candidate
        if candidate.path is None:
            raise RuntimeError("Persisted cleanup path is unavailable")
        return (
            CleanupItemResult(
                item_ref=candidate.item_ref,
                operation_id=item.operation_id,
                sequence=item.sequence,
                source_candidate_id=candidate.source_candidate_id,
                path=candidate.path,
                state=CleanupItemState.VERIFIED if verified else CleanupItemState.FAILED,
                verification_status=verification,
                recycle_result=outcome,
                recovery_id=recovery.recovery_id,
                message=message,
            ),
            recovery,
        )

    def _pre_dispatch_failure(
        self,
        plan: CleanupExecutionPlan,
        item: PlannedCleanupItem,
        message: str,
    ) -> None:
        candidate = item.candidate
        if candidate.path is None:
            raise RuntimeError("Persisted cleanup path is unavailable")
        result = CleanupItemResult(
            item_ref=candidate.item_ref,
            operation_id=item.operation_id,
            sequence=item.sequence,
            source_candidate_id=candidate.source_candidate_id,
            path=candidate.path,
            state=CleanupItemState.BLOCKED_CHANGED,
            verification_status=CleanupVerificationStatus.UNKNOWN,
            message=message,
        )
        self._repository.complete_item(result, None)
        self._audit.item_completed(plan, result, None)

    def _finalize(
        self,
        plan: CleanupExecutionPlan,
        failure: bool,
        cancellation_seen: bool,
    ) -> CleanupResult:
        results = self._repository.list_results(plan.transaction_id)
        verified = sum(item.state is CleanupItemState.VERIFIED for item in results)
        failed = sum(item.state is CleanupItemState.FAILED for item in results)
        blocked = sum(
            item.state in {CleanupItemState.BLOCKED_CHANGED, CleanupItemState.IDENTITY_CHANGED}
            for item in results
        )
        skipped = sum(item.state is CleanupItemState.SKIPPED for item in results)
        if verified == plan.total_items:
            state = CleanupTransactionState.COMPLETED
        elif verified:
            state = CleanupTransactionState.PARTIALLY_COMPLETED
        elif cancellation_seen and not failure:
            state = CleanupTransactionState.CANCELLED
        else:
            state = CleanupTransactionState.FAILED
        self._repository.transition(plan.transaction_id, state)
        successful_refs = {
            item.item_ref for item in results if item.state is CleanupItemState.VERIFIED
        }
        removed_bytes = sum(
            item.candidate.material.tree.total_size_bytes
            for item in plan.items
            if item.candidate.item_ref in successful_refs and item.candidate.material is not None
        )
        recovery = self._repository.list_recovery(plan.transaction_id)
        return CleanupResult(
            transaction_id=plan.transaction_id,
            plan_id=plan.plan_id,
            verified_items=verified,
            failed_items=failed,
            blocked_items=blocked,
            skipped_items=skipped,
            bytes_removed_from_original_locations=removed_bytes,
            verified_disk_space_reclaimed_bytes=None,
            observed_free_space_change_bytes=None,
            recovery_records=tuple(record.recovery_id for record in recovery),
            final_state=state,
            results=results,
        )

    def _block(self, plan: CleanupExecutionPlan, phase: str, exc: Exception) -> None:
        self._repository.transition(
            plan.transaction_id,
            CleanupTransactionState.BLOCKED,
            error_message="Fresh cleanup revalidation failed",
        )
        self._audit.blocked(
            transaction_id=str(plan.transaction_id),
            plan_id=str(plan.plan_id),
            phase=phase,
            error=type(exc).__name__,
        )
