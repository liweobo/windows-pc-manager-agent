"""End-to-end backed-up startup planning, confirmation, execution, and verification."""

from __future__ import annotations

from uuid import UUID, uuid4

from pc_manager_agent.audit.startup_actions import StartupActionAuditLogger
from pc_manager_agent.confirmation.startup_actions import (
    StartupActionConfirmation,
    StartupActionConfirmationService,
)
from pc_manager_agent.domain.startup_actions import (
    DisabledStartupRecord,
    StartupActionPlan,
    StartupActionPreview,
    StartupActionRequest,
    StartupActionType,
    StartupBackupReference,
    StartupEntryStatus,
    StartupErrorCode,
    StartupManagementMode,
    StartupMutationResult,
    StartupObservation,
    StartupSafetyAssessment,
    StartupTransactionState,
)
from pc_manager_agent.domain.startup_errors import StartupActionError
from pc_manager_agent.orchestration.startup_target_resolver import StartupTargetResolver
from pc_manager_agent.persistence.startup_actions import (
    StartupActionRepository,
    StartupBackupVault,
)
from pc_manager_agent.platform_support.startup import StartupManagementPlatform
from pc_manager_agent.safety.startup_policy import StartupSafetyPolicy
from pc_manager_agent.safety.startup_preview import StartupPreviewEngine
from pc_manager_agent.safety.startup_validator import (
    StartupActionSafetyValidator,
    StartupSafetyReview,
)
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


class StartupActionService:
    """Coordinate startup actions without trusting UI state or model-authored locations."""

    def __init__(
        self,
        platform: StartupManagementPlatform,
        resolver: StartupTargetResolver,
        policy: StartupSafetyPolicy,
        preview_engine: StartupPreviewEngine,
        validator: StartupActionSafetyValidator,
        confirmation: StartupActionConfirmationService,
        vault: StartupBackupVault,
        repository: StartupActionRepository,
        registry: ToolRegistry,
        audit: StartupActionAuditLogger,
    ) -> None:
        self._platform = platform
        self._resolver = resolver
        self._policy = policy
        self._preview_engine = preview_engine
        self._validator = validator
        self._confirmation = confirmation
        self._vault = vault
        self._repository = repository
        self._registry = registry
        self._audit = audit

    def list_current(
        self,
    ) -> tuple[tuple[StartupObservation, StartupSafetyAssessment], ...]:
        """Return current inventory paired with deterministic disable assessments."""
        return tuple(
            (item, self._policy.assess(item, StartupActionType.DISABLE))
            for item in self._resolver.list_current()
        )

    def list_disabled(self) -> tuple[DisabledStartupRecord, ...]:
        """Return Agent-disabled entries that still have restorable durable records."""
        return self._repository.list_disabled()

    def prepare_disable(
        self,
        user_goal: str,
        identity: object,
    ) -> tuple[StartupActionPlan, StartupActionPreview, StartupSafetyReview]:
        """Re-resolve one selected identity, back it up, and persist a reviewed Preview."""
        from pc_manager_agent.domain.startup_actions import StartupIdentity

        typed_identity = StartupIdentity.model_validate(identity)
        observation = self._resolver.resolve_identity(typed_identity)
        assessment = self._policy.assess(observation, StartupActionType.DISABLE)
        if assessment.decision.value != "ALLOW":
            raise StartupActionError(
                assessment.reason_codes[0],
                assessment.explanation,
            )
        backup_id = uuid4()
        payload = self._platform.capture_backup(observation.identity, backup_id)
        backup = self._vault.store(payload, backup_id=backup_id)
        plan = self._build_plan(user_goal, StartupActionType.DISABLE, observation, backup)
        return self._prepare(plan, observation, backup)

    def prepare_restore(
        self,
        user_goal: str,
        backup_id: UUID,
    ) -> tuple[StartupActionPlan, StartupActionPreview, StartupSafetyReview]:
        """Prepare restore only from the durable Agent-disabled index and verified vault."""
        record = self._repository.get_disabled(backup_id)
        payload = self._vault.load(backup_id, expected_digest=record.backup_digest)
        if payload.original_identity.canonical_digest() != record.identity.canonical_digest():
            raise StartupActionError(
                StartupErrorCode.BACKUP_CORRUPT,
                "Startup backup identity no longer matches its disabled index",
            )
        if self._platform.inspect(record.identity) is not None:
            raise StartupActionError(
                StartupErrorCode.DESTINATION_CONFLICT,
                "The original startup location is occupied; restore will not overwrite it",
            )
        if not self._platform.disabled_material_matches(payload):
            raise StartupActionError(
                StartupErrorCode.BACKUP_CORRUPT,
                "Agent-disabled material is absent or changed",
            )
        observation = _restore_observation(record)
        backup = StartupBackupReference(
            backup_id=record.backup_id,
            identity_digest=record.identity.canonical_digest(),
            payload_digest=record.backup_digest,
            source=record.identity.source,
            verified=True,
        )
        plan = self._build_plan(user_goal, StartupActionType.RESTORE, observation, backup)
        return self._prepare(plan, observation, backup)

    def request_plan_confirmation(
        self,
        plan: StartupActionPlan,
        preview: StartupActionPreview,
    ) -> StartupActionConfirmation:
        """Issue and persist the first exact startup confirmation."""
        request = self._confirmation.request_plan(plan, preview)
        self._repository.record_confirmation(request)
        self._repository.bind_confirmation(
            plan.transaction_id,
            request.confirmation_id,
            runtime=False,
        )
        return request

    def resolve_plan_confirmation(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: StartupActionPlan,
        preview: StartupActionPreview,
    ) -> StartupActionConfirmation:
        """Resolve the plan gate; rejection cancels this transaction."""
        resolved = self._confirmation.resolve_plan(
            confirmation_id,
            approved,
            plan,
            preview,
        )
        self._repository.record_confirmation(resolved)
        self._audit.confirmation_resolved(plan, resolved)
        self._repository.transition(
            plan.transaction_id,
            (
                StartupTransactionState.AWAITING_RUNTIME_CONFIRMATION
                if approved
                else StartupTransactionState.CANCELLED
            ),
        )
        return resolved

    def request_runtime_confirmation(
        self,
        plan_confirmation_id: UUID,
        plan: StartupActionPlan,
    ) -> tuple[StartupActionPreview, StartupActionConfirmation]:
        """Re-read exact state and backup before issuing the short-lived runtime gate."""
        observation, backup = self._revalidate(plan)
        preview = self._preview_engine.build(plan, observation, backup)
        review = self._validator.review(plan, preview)
        if not review.approved:
            self._block(plan, "; ".join(review.issues))
        request = self._confirmation.request_runtime(
            plan_confirmation_id,
            plan,
            preview,
        )
        self._repository.bind_runtime_preview(plan.transaction_id, preview)
        self._repository.record_confirmation(request)
        self._repository.bind_confirmation(
            plan.transaction_id,
            request.confirmation_id,
            runtime=True,
        )
        return preview, request

    def resolve_runtime_confirmation(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: StartupActionPlan,
        preview: StartupActionPreview,
    ) -> StartupActionConfirmation:
        """Resolve the immediate gate without permitting action or backup substitution."""
        resolved = self._confirmation.resolve_runtime(
            confirmation_id,
            approved,
            plan,
            preview,
        )
        self._repository.record_confirmation(resolved)
        self._audit.confirmation_resolved(plan, resolved)
        self._repository.transition(
            plan.transaction_id,
            StartupTransactionState.CONFIRMED if approved else StartupTransactionState.CANCELLED,
        )
        return resolved

    def execute(
        self,
        plan_confirmation_id: UUID,
        runtime_confirmation_id: UUID,
        plan: StartupActionPlan,
        preview: StartupActionPreview,
        cancellation: CancellationToken | None = None,
    ) -> StartupMutationResult:
        """Consume approvals, revalidate, invoke one narrow tool, verify, persist, and audit."""
        token = cancellation or CancellationToken()
        consumed = self._confirmation.consume_runtime(runtime_confirmation_id, plan, preview)
        if consumed.parent_confirmation_id != plan_confirmation_id:
            raise StartupActionError(
                StartupErrorCode.CONFIRMATION_REPLAYED,
                "Runtime confirmation does not belong to the supplied plan confirmation",
            )
        self._repository.consume_confirmation_pair(consumed.confirmation_id)
        self._repository.record_confirmation(consumed)
        self._repository.transition(plan.transaction_id, StartupTransactionState.VALIDATING)
        current_observation, backup = self._revalidate(plan)
        current_preview = self._preview_engine.build(plan, current_observation, backup)
        review = self._validator.review(plan, current_preview)
        if (
            not review.approved
            or current_preview.current_state_digest != preview.current_state_digest
            or current_preview.assessment.safety_class != preview.assessment.safety_class
            or current_preview.backup_digest != preview.backup_digest
        ):
            self._block(plan, "Startup state changed after confirmation")
        tool_name = _tool_name(plan.action)
        arguments = self._arguments(plan)
        self._repository.transition(plan.transaction_id, StartupTransactionState.EXECUTING)
        try:
            self._audit.started(plan, current_preview, runtime_confirmation_id)
        except Exception:
            self._repository.transition(
                plan.transaction_id,
                StartupTransactionState.FAILED,
                error_code=StartupErrorCode.AUDIT_UNAVAILABLE,
                error_message="Mandatory startup pre-execution audit failed",
            )
            raise
        authorization = ExecutionAuthorization(
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            tool_name=tool_name,
            arguments_digest=arguments_digest(arguments),
            runtime_confirmation_id=runtime_confirmation_id,
        )
        try:
            result = self._registry.execute(
                tool_name,
                arguments,
                token,
                authorization=authorization,
            )
        except Exception as exc:
            self._repository.transition(
                plan.transaction_id,
                StartupTransactionState.FAILED,
                error_code=StartupErrorCode.PLATFORM_ERROR,
                error_message=f"Startup tool failed: {type(exc).__name__}",
            )
            self._audit.failed(
                plan,
                phase="platform_execution",
                error_code=StartupErrorCode.PLATFORM_ERROR.value,
                message=f"{type(exc).__name__}: {exc}",
                mutation_may_have_started=True,
            )
            raise
        if not isinstance(result, StartupMutationResult):
            raise TypeError("Startup tool returned an invalid result")
        self._repository.transition(plan.transaction_id, StartupTransactionState.VERIFYING)
        if not result.verified:
            self._repository.transition(
                plan.transaction_id,
                StartupTransactionState.FAILED,
                error_code=StartupErrorCode.VERIFICATION_FAILED,
                error_message=result.message,
                result=result.model_dump(mode="json"),
            )
            self._audit.completed(plan, result)
            return result
        if plan.action is StartupActionType.DISABLE:
            self._repository.record_disabled(plan.transaction_id, current_preview)
        else:
            self._repository.mark_restored(plan.backup_id)
        self._repository.transition(
            plan.transaction_id,
            StartupTransactionState.COMPLETED,
            result=result.model_dump(mode="json"),
        )
        self._audit.completed(plan, result)
        return result

    def _prepare(
        self,
        plan: StartupActionPlan,
        observation: StartupObservation,
        backup: StartupBackupReference,
    ) -> tuple[StartupActionPlan, StartupActionPreview, StartupSafetyReview]:
        preview = self._preview_engine.build(plan, observation, backup)
        review = self._validator.review(plan, preview)
        self._repository.create(plan, preview, _tool_name(plan.action), self._arguments(plan))
        self._repository.transition(plan.transaction_id, StartupTransactionState.PREVIEWED)
        try:
            self._audit.previewed(plan, preview)
        except Exception:
            self._repository.transition(
                plan.transaction_id,
                StartupTransactionState.BLOCKED,
                error_code=StartupErrorCode.AUDIT_UNAVAILABLE,
                error_message="Mandatory startup Preview audit failed",
            )
            raise
        self._repository.transition(
            plan.transaction_id,
            (
                StartupTransactionState.AWAITING_CONFIRMATION
                if review.approved
                else StartupTransactionState.BLOCKED
            ),
            error_code=None if review.approved else StartupErrorCode.UNSUPPORTED_SOURCE,
            error_message=None if review.approved else "; ".join(review.issues),
        )
        return plan, preview, review

    def _build_plan(
        self,
        user_goal: str,
        action: StartupActionType,
        observation: StartupObservation,
        backup: StartupBackupReference,
    ) -> StartupActionPlan:
        return StartupActionPlan(
            user_goal=user_goal,
            summary=(
                "Disable one exact current-user startup entry with verified backup"
                if action is StartupActionType.DISABLE
                else "Restore one exact Agent-disabled startup entry from verified backup"
            ),
            action=action,
            target_identity=observation.identity,
            target_name=observation.display_name,
            expected_state_digest=observation.current_state_digest(),
            backup_id=backup.backup_id,
            backup_digest=backup.payload_digest,
        )

    def _revalidate(
        self,
        plan: StartupActionPlan,
    ) -> tuple[StartupObservation, StartupBackupReference]:
        payload = self._vault.load(plan.backup_id, expected_digest=plan.backup_digest)
        if payload.original_identity.canonical_digest() != plan.target_identity.canonical_digest():
            raise StartupActionError(
                StartupErrorCode.BACKUP_CORRUPT,
                "Startup backup identity changed",
            )
        if plan.action is StartupActionType.DISABLE:
            observation = self._resolver.resolve_identity(plan.target_identity)
        else:
            record = self._repository.get_disabled(plan.backup_id)
            if self._platform.inspect(record.identity) is not None:
                raise StartupActionError(
                    StartupErrorCode.DESTINATION_CONFLICT,
                    "Startup restore target is now occupied",
                )
            if not self._platform.disabled_material_matches(payload):
                raise StartupActionError(
                    StartupErrorCode.IDENTITY_CHANGED,
                    "Agent-disabled startup material changed",
                )
            observation = _restore_observation(record)
        if observation.current_state_digest() != plan.expected_state_digest:
            raise StartupActionError(
                StartupErrorCode.IDENTITY_CHANGED,
                "Startup configuration changed; generate a new Preview",
            )
        return observation, StartupBackupReference(
            backup_id=plan.backup_id,
            identity_digest=plan.target_identity.canonical_digest(),
            payload_digest=plan.backup_digest,
            source=plan.target_identity.source,
            verified=True,
        )

    def _block(self, plan: StartupActionPlan, message: str) -> None:
        self._repository.transition(
            plan.transaction_id,
            StartupTransactionState.BLOCKED,
            error_code=StartupErrorCode.IDENTITY_CHANGED,
            error_message=message,
        )
        self._audit.failed(
            plan,
            phase="execution_revalidation",
            error_code=StartupErrorCode.IDENTITY_CHANGED.value,
            message=message,
            mutation_may_have_started=False,
        )
        raise StartupActionError(StartupErrorCode.IDENTITY_CHANGED, message)

    @staticmethod
    def _arguments(plan: StartupActionPlan) -> dict[str, object]:
        return StartupActionRequest(
            action=plan.action,
            identity=plan.target_identity,
            expected_state_digest=plan.expected_state_digest,
            backup_id=plan.backup_id,
            backup_digest=plan.backup_digest,
        ).model_dump(mode="json")


def _restore_observation(record: DisabledStartupRecord) -> StartupObservation:
    return record.original_observation.model_copy(
        update={
            "status": StartupEntryStatus.AGENT_DISABLED,
            "status_evidence": "Exact Agent backup and disabled material were verified",
            "management_mode": StartupManagementMode.RESTORE_SUPPORTED,
        }
    )


def _tool_name(action: StartupActionType) -> str:
    return "startup.disable" if action is StartupActionType.DISABLE else "startup.restore"
