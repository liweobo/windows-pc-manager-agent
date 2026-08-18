"""Backed-up planning, confirmation, execution, verification, and restore for Stage 4C2."""

from __future__ import annotations

from uuid import UUID, uuid4

from pc_manager_agent.audit.service_startup_actions import ServiceStartupActionAuditLogger
from pc_manager_agent.confirmation.service_startup_actions import (
    ServiceStartupActionConfirmation,
    ServiceStartupActionConfirmationService,
)
from pc_manager_agent.domain.service_actions import (
    ServiceObservation,
    ServiceStableIdentity,
    ServiceStartupConfiguration,
    ServiceStartupType,
)
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionPlan,
    ServiceStartupActionPreview,
    ServiceStartupActionRequest,
    ServiceStartupActionType,
    ServiceStartupBackupPayload,
    ServiceStartupBackupReference,
    ServiceStartupChangeRecord,
    ServiceStartupErrorCode,
    ServiceStartupMutationResult,
    ServiceStartupPermissionEvidence,
    ServiceStartupTransactionState,
)
from pc_manager_agent.domain.service_startup_errors import ServiceStartupActionError
from pc_manager_agent.orchestration.service_target_resolver import ServiceTargetResolver
from pc_manager_agent.persistence.service_startup_actions import (
    ServiceStartupActionRepository,
    ServiceStartupBackupVault,
    ServiceStartupStoreError,
)
from pc_manager_agent.platform_support.service_control import ServiceControlPlatform
from pc_manager_agent.platform_support.service_startup import ServiceStartupPlatform
from pc_manager_agent.safety.service_startup_policy import (
    ServiceStartupSafetyPolicy,
    build_service_startup_impact,
)
from pc_manager_agent.safety.service_startup_preview import ServiceStartupPreviewEngine
from pc_manager_agent.safety.service_startup_validator import (
    ServiceStartupSafetyReview,
    ServiceStartupSafetyValidator,
)
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


class ServiceStartupActionService:
    """Coordinate exact startup writes without trusting UI state or model-authored identity."""

    def __init__(
        self,
        control_platform: ServiceControlPlatform,
        startup_platform: ServiceStartupPlatform,
        resolver: ServiceTargetResolver,
        policy: ServiceStartupSafetyPolicy,
        preview_engine: ServiceStartupPreviewEngine,
        validator: ServiceStartupSafetyValidator,
        confirmation: ServiceStartupActionConfirmationService,
        vault: ServiceStartupBackupVault,
        repository: ServiceStartupActionRepository,
        registry: ToolRegistry,
        audit: ServiceStartupActionAuditLogger,
    ) -> None:
        self._control_platform = control_platform
        self._startup_platform = startup_platform
        self._resolver = resolver
        self._policy = policy
        self._preview_engine = preview_engine
        self._validator = validator
        self._confirmation = confirmation
        self._vault = vault
        self._repository = repository
        self._registry = registry
        self._audit = audit

    def list_changes(self) -> tuple[ServiceStartupChangeRecord, ...]:
        """Return Agent-owned verified changes that remain conflict-check restorable."""
        return self._repository.list_changes()

    def prepare_change(
        self,
        user_goal: str,
        identity: object,
        action: ServiceStartupActionType,
    ) -> tuple[
        ServiceStartupActionPlan,
        ServiceStartupActionPreview,
        ServiceStartupSafetyReview,
    ]:
        """Resolve one service, create verified backup, and persist a reviewed Preview."""
        if action is ServiceStartupActionType.RESTORE:
            raise ValueError("Use prepare_restore for restore transactions")
        typed_identity = ServiceStableIdentity.model_validate(identity)
        observation = self._resolve_exact_identity(typed_identity)
        target = _target_for_action(action)
        self._require_eligible(observation, action, target)
        permissions = self._startup_platform.evaluate_permissions(observation.identity.service_name)
        self._require_permissions(permissions)
        backup = self._create_backup(observation)
        plan = self._build_plan(
            user_goal,
            action,
            observation,
            target,
            permissions,
            backup,
        )
        return self._prepare(plan, observation, permissions, backup)

    def prepare_restore(
        self,
        user_goal: str,
        source_backup_id: UUID,
    ) -> tuple[
        ServiceStartupActionPlan,
        ServiceStartupActionPreview,
        ServiceStartupSafetyReview,
    ]:
        """Prepare restore only when current config exactly matches Agent-written state."""
        record = self._repository.get_change(source_backup_id)
        original = self._vault.load(
            record.backup_id,
            expected_digest=record.backup_digest,
        )
        if (
            original.stable_identity.canonical_digest() != record.stable_identity.canonical_digest()
            or original.original_configuration != record.original_configuration
        ):
            raise ServiceStartupActionError(
                ServiceStartupErrorCode.BACKUP_CORRUPT,
                "Restore source backup no longer matches its change history",
            )
        observation = self._resolve_exact_identity(record.stable_identity)
        if observation.startup_configuration != record.written_configuration:
            raise ServiceStartupActionError(
                ServiceStartupErrorCode.RESTORE_CONFLICT,
                "Current startup configuration differs from the Agent-written state",
            )
        self._require_eligible(
            observation,
            ServiceStartupActionType.RESTORE,
            record.original_configuration,
        )
        permissions = self._startup_platform.evaluate_permissions(observation.identity.service_name)
        self._require_permissions(permissions)
        current_backup = self._create_backup(observation)
        plan = self._build_plan(
            user_goal,
            ServiceStartupActionType.RESTORE,
            observation,
            record.original_configuration,
            permissions,
            current_backup,
            restore_source_backup_id=record.backup_id,
        )
        return self._prepare(plan, observation, permissions, current_backup)

    def request_plan_confirmation(
        self,
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
    ) -> ServiceStartupActionConfirmation:
        """Issue and persist the first exact configuration confirmation."""
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
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
    ) -> ServiceStartupActionConfirmation:
        """Resolve the plan gate; rejection cancels the durable transaction."""
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
                ServiceStartupTransactionState.AWAITING_RUNTIME_CONFIRMATION
                if approved
                else ServiceStartupTransactionState.CANCELLED
            ),
        )
        return resolved

    def request_runtime_confirmation(
        self,
        plan_confirmation_id: UUID,
        plan: ServiceStartupActionPlan,
    ) -> tuple[ServiceStartupActionPreview, ServiceStartupActionConfirmation]:
        """Re-read every binding before issuing the short-lived immediate gate."""
        observation, permissions, backup = self._revalidate(plan)
        preview = self._preview_engine.build(
            plan,
            observation,
            plan.target_configuration,
            permissions,
            backup,
        )
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
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
    ) -> ServiceStartupActionConfirmation:
        """Resolve the immediate gate without permitting transition substitution."""
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
            (
                ServiceStartupTransactionState.CONFIRMED
                if approved
                else ServiceStartupTransactionState.CANCELLED
            ),
        )
        return resolved

    def execute(
        self,
        plan_confirmation_id: UUID,
        runtime_confirmation_id: UUID,
        plan: ServiceStartupActionPlan,
        preview: ServiceStartupActionPreview,
        cancellation: CancellationToken | None = None,
    ) -> ServiceStartupMutationResult:
        """Consume approvals, revalidate, run one tool, verify, journal, and audit."""
        token = cancellation or CancellationToken()
        consumed = self._confirmation.consume_runtime(
            runtime_confirmation_id,
            plan,
            preview,
        )
        if consumed.parent_confirmation_id != plan_confirmation_id:
            raise ServiceStartupActionError(
                ServiceStartupErrorCode.CONFIRMATION_REPLAYED,
                "Runtime confirmation does not belong to the supplied plan confirmation",
            )
        self._repository.consume_confirmation_pair(consumed.confirmation_id)
        self._repository.record_confirmation(consumed)
        self._repository.transition(
            plan.transaction_id,
            ServiceStartupTransactionState.VALIDATING,
        )
        observation, permissions, backup = self._revalidate(plan)
        current_preview = self._preview_engine.build(
            plan,
            observation,
            plan.target_configuration,
            permissions,
            backup,
        )
        review = self._validator.review(plan, current_preview)
        if (
            not review.approved
            or current_preview.current_state_digest != preview.current_state_digest
            or current_preview.impact.canonical_digest() != preview.impact.canonical_digest()
            or current_preview.permissions.canonical_digest()
            != preview.permissions.canonical_digest()
        ):
            self._block(plan, "Service startup evidence changed after confirmation")
        tool_name = _tool_name(plan.action)
        arguments = self._arguments(plan)
        self._repository.transition(
            plan.transaction_id,
            ServiceStartupTransactionState.EXECUTING,
        )
        try:
            self._audit.started(plan, current_preview, runtime_confirmation_id)
        except Exception:
            self._repository.transition(
                plan.transaction_id,
                ServiceStartupTransactionState.FAILED,
                error_code=ServiceStartupErrorCode.AUDIT_UNAVAILABLE,
                error_message="Mandatory pre-execution audit failed",
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
            code = getattr(exc, "code", ServiceStartupErrorCode.PLATFORM_ERROR)
            self._repository.transition(
                plan.transaction_id,
                ServiceStartupTransactionState.FAILED,
                error_code=code,
                error_message=f"Service startup tool failed: {type(exc).__name__}",
            )
            self._audit.failed(
                plan,
                phase="platform_execution",
                error_code=getattr(code, "value", str(code)),
                message=f"{type(exc).__name__}: {exc}",
                mutation_may_have_started=True,
            )
            raise
        if not isinstance(result, ServiceStartupMutationResult):
            raise TypeError("Service startup tool returned an invalid result")
        self._repository.transition(
            plan.transaction_id,
            ServiceStartupTransactionState.VERIFYING,
        )
        if not result.verified or not result.runtime_unchanged:
            self._repository.transition(
                plan.transaction_id,
                ServiceStartupTransactionState.FAILED,
                error_code=ServiceStartupErrorCode.VERIFICATION_FAILED,
                error_message=result.message,
                result=result.model_dump(mode="json"),
            )
            self._audit.completed(plan, result)
            return result
        try:
            self._repository.record_change(
                plan,
                current_preview,
                restored_source_backup_id=plan.restore_source_backup_id,
            )
        except ServiceStartupStoreError as exc:
            self._repository.transition(
                plan.transaction_id,
                ServiceStartupTransactionState.FAILED,
                error_code=ServiceStartupErrorCode.JOURNAL_UNAVAILABLE,
                error_message="Verified write could not be indexed for safe restore",
                result=result.model_dump(mode="json"),
            )
            self._audit.failed(
                plan,
                phase="restore_history_persistence",
                error_code=ServiceStartupErrorCode.JOURNAL_UNAVAILABLE.value,
                message=str(exc),
                mutation_may_have_started=True,
            )
            raise ServiceStartupActionError(
                ServiceStartupErrorCode.JOURNAL_UNAVAILABLE,
                "Configuration changed but restore history could not be persisted",
            ) from exc
        self._repository.transition(
            plan.transaction_id,
            ServiceStartupTransactionState.COMPLETED,
            result=result.model_dump(mode="json"),
        )
        self._audit.completed(plan, result)
        return result

    def _prepare(
        self,
        plan: ServiceStartupActionPlan,
        observation: ServiceObservation,
        permissions: ServiceStartupPermissionEvidence,
        backup: ServiceStartupBackupReference,
    ) -> tuple[
        ServiceStartupActionPlan,
        ServiceStartupActionPreview,
        ServiceStartupSafetyReview,
    ]:
        preview = self._preview_engine.build(
            plan,
            observation,
            plan.target_configuration,
            permissions,
            backup,
        )
        review = self._validator.review(plan, preview)
        self._repository.create(plan, preview, _tool_name(plan.action), self._arguments(plan))
        self._repository.transition(
            plan.transaction_id,
            ServiceStartupTransactionState.PREVIEWED,
        )
        try:
            self._audit.previewed(plan, preview)
        except Exception:
            self._repository.transition(
                plan.transaction_id,
                ServiceStartupTransactionState.BLOCKED,
                error_code=ServiceStartupErrorCode.AUDIT_UNAVAILABLE,
                error_message="Mandatory service startup Preview audit failed",
            )
            raise
        self._repository.transition(
            plan.transaction_id,
            (
                ServiceStartupTransactionState.AWAITING_CONFIRMATION
                if review.approved
                else ServiceStartupTransactionState.BLOCKED
            ),
            error_code=(
                None if review.approved else ServiceStartupErrorCode.UNSUPPORTED_TRANSITION
            ),
            error_message=None if review.approved else "; ".join(review.issues),
        )
        return plan, preview, review

    def _build_plan(
        self,
        user_goal: str,
        action: ServiceStartupActionType,
        observation: ServiceObservation,
        target: ServiceStartupConfiguration,
        permissions: ServiceStartupPermissionEvidence,
        backup: ServiceStartupBackupReference,
        *,
        restore_source_backup_id: UUID | None = None,
    ) -> ServiceStartupActionPlan:
        impact = build_service_startup_impact(observation)
        return ServiceStartupActionPlan(
            user_goal=user_goal,
            summary=(
                f"Change exact service {observation.identity.service_name} startup type from "
                f"{observation.startup_configuration.startup_type.value} to "
                f"{target.startup_type.value} without changing runtime state"
            ),
            action=action,
            target_identity=observation.identity,
            display_name=observation.display_name,
            source_configuration=observation.startup_configuration,
            target_configuration=target,
            expected_runtime_state=observation.state,
            expected_state_digest=observation.state_digest(),
            expected_impact_digest=impact.canonical_digest(),
            expected_permission_digest=permissions.canonical_digest(),
            backup_id=backup.backup_id,
            backup_digest=backup.payload_digest,
            restore_source_backup_id=restore_source_backup_id,
        )

    def _create_backup(
        self,
        observation: ServiceObservation,
    ) -> ServiceStartupBackupReference:
        backup_id = uuid4()
        payload = ServiceStartupBackupPayload(
            stable_identity=observation.identity,
            display_name=observation.display_name,
            original_configuration=observation.startup_configuration,
            original_runtime_state=observation.state,
        )
        try:
            return self._vault.store(payload, backup_id=backup_id)
        except ServiceStartupStoreError as exc:
            raise ServiceStartupActionError(
                ServiceStartupErrorCode.BACKUP_UNAVAILABLE,
                "Verified service startup backup could not be created",
            ) from exc

    def _revalidate(
        self,
        plan: ServiceStartupActionPlan,
    ) -> tuple[
        ServiceObservation,
        ServiceStartupPermissionEvidence,
        ServiceStartupBackupReference,
    ]:
        payload = self._vault.load(plan.backup_id, expected_digest=plan.backup_digest)
        if (
            payload.stable_identity.canonical_digest() != plan.target_identity.canonical_digest()
            or payload.original_configuration != plan.source_configuration
            or payload.original_runtime_state is not plan.expected_runtime_state
        ):
            raise ServiceStartupActionError(
                ServiceStartupErrorCode.BACKUP_CORRUPT,
                "Service startup backup changed",
            )
        observation = self._resolve_exact_identity(plan.target_identity)
        if observation.state_digest() != plan.expected_state_digest:
            raise ServiceStartupActionError(
                ServiceStartupErrorCode.CONFIGURATION_CHANGED,
                "Service startup configuration or runtime state changed",
            )
        impact = build_service_startup_impact(observation)
        if impact.canonical_digest() != plan.expected_impact_digest:
            raise ServiceStartupActionError(
                ServiceStartupErrorCode.DEPENDENCY_IMPACT_BLOCKED,
                "Service dependency impact changed",
            )
        permissions = self._startup_platform.evaluate_permissions(observation.identity.service_name)
        if permissions.canonical_digest() != plan.expected_permission_digest:
            raise ServiceStartupActionError(
                ServiceStartupErrorCode.PRIVILEGE_REQUIRED,
                "Service configuration permission evidence changed",
            )
        if plan.action is ServiceStartupActionType.RESTORE:
            source_id = plan.restore_source_backup_id
            if source_id is None:
                raise ServiceStartupActionError(
                    ServiceStartupErrorCode.RESTORE_CONFLICT,
                    "Restore source is missing",
                )
            record = self._repository.get_change(source_id)
            if (
                record.stable_identity.canonical_digest() != plan.target_identity.canonical_digest()
                or record.written_configuration != observation.startup_configuration
                or record.original_configuration != plan.target_configuration
            ):
                raise ServiceStartupActionError(
                    ServiceStartupErrorCode.RESTORE_CONFLICT,
                    "Current configuration no longer matches the Agent-written state",
                )
        return (
            observation,
            permissions,
            ServiceStartupBackupReference(
                backup_id=plan.backup_id,
                identity_digest=plan.target_identity.canonical_digest(),
                payload_digest=plan.backup_digest,
                verified=True,
            ),
        )

    def _resolve_exact_identity(
        self,
        identity: ServiceStableIdentity,
    ) -> ServiceObservation:
        observation = self._resolver.resolve_name(identity.service_name)
        if observation.identity.canonical_digest() != identity.canonical_digest():
            raise ServiceStartupActionError(
                ServiceStartupErrorCode.IDENTITY_CHANGED,
                "Stable service identity changed",
            )
        return observation

    def _require_eligible(
        self,
        observation: ServiceObservation,
        action: ServiceStartupActionType,
        target: ServiceStartupConfiguration,
    ) -> None:
        assessment = self._policy.assess(observation, action, target)
        if not assessment.allowed:
            raise ServiceStartupActionError(
                assessment.reason_codes[0],
                assessment.explanation,
            )

    @staticmethod
    def _require_permissions(permissions: ServiceStartupPermissionEvidence) -> None:
        if permissions.process_elevated:
            raise ServiceStartupActionError(
                ServiceStartupErrorCode.ELEVATED_PROCESS_BLOCKED,
                "Stage 4C2 does not run from an elevated process",
            )
        if not permissions.allows_change:
            raise ServiceStartupActionError(
                ServiceStartupErrorCode.PRIVILEGE_REQUIRED,
                "Existing SERVICE_CHANGE_CONFIG permission is unavailable; no elevation offered",
            )

    def _block(self, plan: ServiceStartupActionPlan, message: str) -> None:
        self._repository.transition(
            plan.transaction_id,
            ServiceStartupTransactionState.BLOCKED,
            error_code=ServiceStartupErrorCode.CONFIGURATION_CHANGED,
            error_message=message,
        )
        self._audit.failed(
            plan,
            phase="execution_revalidation",
            error_code=ServiceStartupErrorCode.CONFIGURATION_CHANGED.value,
            message=message,
            mutation_may_have_started=False,
        )
        raise ServiceStartupActionError(ServiceStartupErrorCode.CONFIGURATION_CHANGED, message)

    @staticmethod
    def _arguments(plan: ServiceStartupActionPlan) -> dict[str, object]:
        return ServiceStartupActionRequest(
            action=plan.action,
            identity=plan.target_identity,
            expected_source_configuration=plan.source_configuration,
            target_configuration=plan.target_configuration,
            expected_runtime_state=plan.expected_runtime_state,
            expected_impact_digest=plan.expected_impact_digest,
            backup_id=plan.backup_id,
            backup_digest=plan.backup_digest,
        ).model_dump(mode="json")


def _target_for_action(action: ServiceStartupActionType) -> ServiceStartupConfiguration:
    if action is ServiceStartupActionType.SET_AUTOMATIC:
        return ServiceStartupConfiguration(
            startup_type=ServiceStartupType.AUTOMATIC,
            delayed_auto_start=False,
        )
    if action is ServiceStartupActionType.SET_MANUAL:
        return ServiceStartupConfiguration(
            startup_type=ServiceStartupType.MANUAL,
            delayed_auto_start=False,
        )
    raise ValueError("RESTORE target must come from an Agent backup")


def _tool_name(action: ServiceStartupActionType) -> str:
    return {
        ServiceStartupActionType.SET_AUTOMATIC: ("system.service.startup.set_automatic"),
        ServiceStartupActionType.SET_MANUAL: "system.service.startup.set_manual",
        ServiceStartupActionType.RESTORE: "system.service.startup.restore",
    }[action]
