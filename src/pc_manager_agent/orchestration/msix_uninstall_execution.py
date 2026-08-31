"""Plan-confirm-revalidate-execute-verify workflow for one MSIX package."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from pc_manager_agent.audit.msix_uninstall import MsixUninstallAuditLogger
from pc_manager_agent.confirmation.msix_uninstall import (
    MsixConfirmationService,
    MsixUninstallConfirmation,
)
from pc_manager_agent.domain.msix_uninstall import (
    MsixPackageType,
    MsixPreflight,
    MsixRemovalDecision,
    MsixTargetQuery,
    MsixTransactionState,
    MsixUninstallPlan,
    MsixUninstallPreview,
    MsixUninstallRequest,
    MsixUninstallResult,
    MsixVerificationState,
    NormalizedMsixPackage,
    ValidatedMsixRemovalAction,
)
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareSafetyClass
from pc_manager_agent.orchestration.msix_uninstall import (
    MsixInventoryService,
    MsixPackageTypeClassifier,
    MsixPreviewService,
    MsixRemovalPolicy,
    MsixTargetResolver,
)
from pc_manager_agent.orchestration.uninstall_context import UninstallContextRecorder
from pc_manager_agent.persistence.msix_uninstall import MsixUninstallRepository
from pc_manager_agent.platform_support.msix_packages import MsixPackagePlatform
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


class MsixUninstallExecutionError(RuntimeError):
    """Raised when a fail-closed Stage 4D2C2 gate rejects execution."""


@dataclass(frozen=True, slots=True)
class PreparedMsixUninstall:
    """Preparation result containing candidates or one exact package Preview."""

    plan: MsixUninstallPlan | None
    preview: MsixUninstallPreview | None
    plan_confirmation: MsixUninstallConfirmation | None
    candidates: tuple[NormalizedMsixPackage, ...] = ()


@dataclass(frozen=True, slots=True)
class PreparedMsixRuntimeConfirmation:
    """Immediate confirmation issued only after fresh identity/dependency validation."""

    preview: MsixUninstallPreview
    confirmation: MsixUninstallConfirmation


class MsixUninstallService:
    """Coordinate deterministic MSIX evidence while the registry alone dispatches writes."""

    def __init__(
        self,
        *,
        platform: MsixPackagePlatform,
        repository: MsixUninstallRepository,
        confirmations: MsixConfirmationService,
        registry: ToolRegistry,
        audit: MsixUninstallAuditLogger,
        preflight: Callable[[NormalizedMsixPackage, bool], MsixPreflight],
        process_is_elevated: Callable[[], bool],
        max_items: int = 5_000,
        preview_ttl_seconds: int = 300,
        context_recorder: UninstallContextRecorder | None = None,
    ) -> None:
        self._platform = platform
        self._inventory = MsixInventoryService(platform, max_items)
        self._resolver = MsixTargetResolver()
        self._classifier = MsixPackageTypeClassifier()
        self._policy = MsixRemovalPolicy()
        self._preview = MsixPreviewService(preview_ttl_seconds)
        self._repository = repository
        self._confirmations = confirmations
        self._registry = registry
        self._audit = audit
        self._preflight = preflight
        self._process_is_elevated = process_is_elevated
        self._context_recorder = context_recorder

    def prepare(
        self,
        user_goal: str,
        query: MsixTargetQuery,
        cancellation: CancellationToken | None = None,
    ) -> PreparedMsixUninstall:
        """Refresh current-user packages and prepare an exact digest-bound Preview."""
        token = cancellation or CancellationToken()
        inventory = self._inventory.collect(token)
        resolution = self._resolver.resolve(query, inventory)
        package = resolution.selected
        if package is None:
            return PreparedMsixUninstall(None, None, None, resolution.candidates)
        package_type = self._classifier.classify(package)
        package = package.model_copy(
            update={"identity": package.identity.model_copy(update={"package_type": package_type})}
        )
        dependencies = self._platform.dependency_snapshot(package.identity)
        preflight = self._preflight(package, self._repository.has_active_uninstall())
        assessment = self._policy.assess(
            package,
            package_type,
            self._safety_class(package_type),
            dependencies,
            preflight,
            process_elevated=self._process_is_elevated(),
        )
        plan = MsixUninstallPlan(
            user_goal=user_goal,
            package_identity_digest=package.identity.canonical_digest(),
            dependency_digest=dependencies.canonical_digest(),
            assessment_digest=assessment.canonical_digest(),
            preflight_digest=preflight.canonical_digest(),
        )
        preview = self._preview.build(plan, package, dependencies, assessment, preflight)
        if assessment.decision is not MsixRemovalDecision.ALLOW or not preview.executable:
            raise MsixUninstallExecutionError(
                "MSIX package is blocked by deterministic safety policy"
            )
        self._repository.create(plan, preview)
        self._audit.previewed(plan, preview)
        confirmation = self._confirmations.request_plan(plan, preview)
        return PreparedMsixUninstall(plan, preview, confirmation)

    def resolve_confirmation(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: MsixUninstallPlan,
        preview: MsixUninstallPreview,
    ) -> MsixUninstallConfirmation:
        """Resolve and audit either the plan or immediate confirmation."""
        confirmation = self._confirmations.approve(confirmation_id, approved, plan, preview)
        self._audit.confirmation_resolved(plan, confirmation)
        return confirmation

    def prepare_runtime_confirmation(
        self,
        plan_confirmation_id: UUID,
        plan: MsixUninstallPlan,
        preview: MsixUninstallPreview,
    ) -> PreparedMsixRuntimeConfirmation:
        """Repeat all package/dependency/preflight facts before immediate approval."""
        self._repository.transition(plan.transaction_id, MsixTransactionState.VALIDATING)
        fresh_preflight = self._preflight(
            preview.package,
            self._repository.has_active_uninstall(plan.transaction_id),
        )
        self._validated_action(preview, fresh_preflight)
        confirmation = self._confirmations.request_runtime(plan_confirmation_id, plan, preview)
        return PreparedMsixRuntimeConfirmation(preview, confirmation)

    def execute(
        self,
        runtime_confirmation_id: UUID,
        plan: MsixUninstallPlan,
        preview: MsixUninstallPreview,
        cancellation: CancellationToken | None = None,
    ) -> MsixUninstallResult:
        """Revalidate, consume approvals, and dispatch exactly one registered tool call."""
        token = cancellation or CancellationToken()
        if token.is_cancelled:
            self._repository.transition(plan.transaction_id, MsixTransactionState.CANCELLED)
            raise MsixUninstallExecutionError("MSIX removal cancelled before dispatch")
        fresh_preflight = self._preflight(
            preview.package,
            self._repository.has_active_uninstall(plan.transaction_id),
        )
        action = self._validated_action(preview, fresh_preflight)
        confirmation = self._confirmations.consume_runtime(runtime_confirmation_id, plan, preview)
        request = MsixUninstallRequest(
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            action=action,
        )
        arguments = request.model_dump(mode="json")
        authorization = ExecutionAuthorization(
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            tool_name=plan.tool_name,
            arguments_digest=arguments_digest(arguments),
            runtime_confirmation_id=confirmation.confirmation_id,
        )
        self._audit.started(plan, preview, str(confirmation.confirmation_id))
        context_id = (
            self._context_recorder.capture_msix(preview)
            if self._context_recorder is not None
            else None
        )
        try:
            result = self._registry.execute(
                plan.tool_name,
                arguments,
                token,
                authorization,
            )
        except Exception:
            if self._context_recorder is not None:
                self._context_recorder.finalize(
                    context_id,
                    verification_state="failed",
                    verified_removed=False,
                )
            raise
        if not isinstance(result, MsixUninstallResult):
            raise MsixUninstallExecutionError("MSIX tool returned an unexpected result")
        final_state = (
            MsixTransactionState.VERIFIED_REMOVED
            if result.verification.state.value in {"verified_removed", "already_removed"}
            else MsixTransactionState.COMPLETED_UNVERIFIED
        )
        self._repository.transition(plan.transaction_id, final_state, result=result)
        self._audit.completed(plan, result)
        if self._context_recorder is not None:
            self._context_recorder.finalize(
                context_id,
                verification_state=result.verification.state.value,
                verified_removed=result.verification.state
                in {
                    MsixVerificationState.VERIFIED_REMOVED,
                    MsixVerificationState.ALREADY_REMOVED,
                },
                completed_unverified=(
                    result.verification.state is MsixVerificationState.COMPLETED_UNVERIFIED
                ),
            )
        return result

    def _validated_action(
        self, preview: MsixUninstallPreview, fresh_preflight: MsixPreflight
    ) -> ValidatedMsixRemovalAction:
        """Rebuild and compare exact identity, relationship, and preflight evidence."""
        inventory = self._inventory.collect(CancellationToken())
        matches = [
            item
            for item in inventory.packages
            if item.identity.instance.full_name == preview.package.identity.instance.full_name
        ]
        if len(matches) != 1:
            raise MsixUninstallExecutionError("MSIX package disappeared or inventory is ambiguous")
        fresh = matches[0].identity
        if fresh.canonical_digest() != preview.package.identity.canonical_digest():
            raise MsixUninstallExecutionError("Package identity changed; old approval is invalid")
        dependencies = self._platform.dependency_snapshot(fresh)
        if dependencies.canonical_digest() != preview.dependencies.canonical_digest():
            raise MsixUninstallExecutionError(
                "Package dependencies changed; old approval is invalid"
            )
        if fresh_preflight.canonical_digest() != preview.preflight.canonical_digest():
            raise MsixUninstallExecutionError("MSIX preflight changed; old approval is invalid")
        return ValidatedMsixRemovalAction(
            transaction_id=preview.transaction_id,
            identity=fresh,
            dependency_digest=dependencies.canonical_digest(),
            assessment_digest=preview.assessment.canonical_digest(),
            preflight_digest=fresh_preflight.canonical_digest(),
            validated_at=preview.generated_at,
        )

    @staticmethod
    def _safety_class(package_type: MsixPackageType) -> SoftwareSafetyClass:
        """Map only ordinary user-app classification into the existing safety taxonomy."""
        if package_type is MsixPackageType.SECURITY:
            return SoftwareSafetyClass.SECURITY_SOFTWARE
        if package_type is MsixPackageType.SYSTEM:
            return SoftwareSafetyClass.WINDOWS_COMPONENT
        if package_type is MsixPackageType.USER_MSIX_APP:
            return SoftwareSafetyClass.USER_APPLICATION
        return SoftwareSafetyClass.UNKNOWN
