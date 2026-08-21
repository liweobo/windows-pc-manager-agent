"""Deterministic Stage 4D2A MSI uninstall orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from pc_manager_agent.audit.repository import AuditUnavailableError
from pc_manager_agent.audit.software_uninstall_execution import MsiUninstallAuditLogger
from pc_manager_agent.confirmation.software_uninstall_execution import (
    MsiUninstallConfirmation,
    MsiUninstallConfirmationService,
)
from pc_manager_agent.domain.software_uninstall_analysis import (
    NormalizedInstalledSoftware,
    RawInstalledSoftwareEntry,
    ResolvedSoftwareTarget,
    SoftwareTargetQuery,
)
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiExecutionDecision,
    MsiInstallerResultCategory,
    MsiResidualReport,
    MsiUninstallExecutionReport,
    MsiUninstallPlan,
    MsiUninstallPreview,
    MsiUninstallRequest,
    MsiUninstallResult,
    MsiUninstallTransactionState,
    MsiUninstallVerification,
    MsiVerificationState,
)
from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
from pc_manager_agent.orchestration.software_execution_preflight import (
    SoftwareExecutionPreflight,
)
from pc_manager_agent.orchestration.software_msi_validation import MsiProductValidator
from pc_manager_agent.orchestration.software_residual_analyzer import SoftwareResidualAnalyzer
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.orchestration.software_uninstall_verifier import MsiUninstallVerifier
from pc_manager_agent.persistence.software_uninstall_execution import MsiUninstallRepository
from pc_manager_agent.safety.software_uninstall_execution_policy import (
    SoftwareUninstallExecutionPolicy,
)
from pc_manager_agent.safety.software_uninstall_execution_preview import (
    MsiUninstallPreviewEngine,
)
from pc_manager_agent.safety.software_uninstall_execution_validator import (
    MsiUninstallSafetyReview,
    MsiUninstallSafetyValidator,
)
from pc_manager_agent.safety.software_uninstall_policy import SoftwareUninstallSafetyPolicy
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


class MsiUninstallExecutionErrorCode(StrEnum):
    """Fail-closed orchestration reasons safe to display and audit."""

    TARGET_AMBIGUOUS = "target_ambiguous"
    TARGET_CHANGED = "target_changed"
    RAW_EVIDENCE_MISSING = "raw_evidence_missing"
    POLICY_BLOCKED = "policy_blocked"
    ELEVATED_PROCESS_BLOCKED = "elevated_process_blocked"
    SAFETY_REVIEW_FAILED = "safety_review_failed"
    EXECUTION_FAILED = "execution_failed"


class MsiUninstallExecutionError(RuntimeError):
    """Raised without exposing raw installer metadata or command strings."""

    def __init__(self, code: MsiUninstallExecutionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PreparedMsiUninstall:
    """Preparation outcome containing candidates or one reviewed execution Preview."""

    resolution: ResolvedSoftwareTarget
    plan: MsiUninstallPlan | None = None
    preview: MsiUninstallPreview | None = None
    review: MsiUninstallSafetyReview | None = None
    plan_confirmation: MsiUninstallConfirmation | None = None


@dataclass(frozen=True, slots=True)
class PreparedMsiRuntimeConfirmation:
    """Fresh pre-dispatch Preview and its short-lived immediate confirmation."""

    preview: MsiUninstallPreview
    confirmation: MsiUninstallConfirmation


class MsiUninstallService:
    """Coordinate identity, policy, confirmations, one tool, verification, and audit."""

    def __init__(
        self,
        resolver: SoftwareTargetResolver,
        capability: UninstallCapabilityResolver,
        product_validator: MsiProductValidator,
        analysis_policy: SoftwareUninstallSafetyPolicy,
        execution_policy: SoftwareUninstallExecutionPolicy,
        preflight: SoftwareExecutionPreflight,
        preview_engine: MsiUninstallPreviewEngine,
        safety_validator: MsiUninstallSafetyValidator,
        confirmations: MsiUninstallConfirmationService,
        repository: MsiUninstallRepository,
        registry: ToolRegistry,
        verifier: MsiUninstallVerifier,
        residual: SoftwareResidualAnalyzer,
        audit: MsiUninstallAuditLogger,
        *,
        process_is_elevated: Callable[[], bool],
        max_items: int = 5_000,
    ) -> None:
        self._resolver = resolver
        self._capability = capability
        self._product_validator = product_validator
        self._analysis_policy = analysis_policy
        self._execution_policy = execution_policy
        self._preflight = preflight
        self._preview_engine = preview_engine
        self._safety_validator = safety_validator
        self._confirmations = confirmations
        self._repository = repository
        self._registry = registry
        self._verifier = verifier
        self._residual = residual
        self._audit = audit
        self._process_is_elevated = process_is_elevated
        self._max_items = max_items

    def prepare(
        self,
        user_goal: str,
        query: SoftwareTargetQuery,
        cancellation: CancellationToken | None = None,
    ) -> PreparedMsiUninstall:
        """Refresh all local evidence and request only the first confirmation."""
        token = cancellation or CancellationToken()
        resolution, _inventory = self._resolver.resolve(query, self._max_items, token)
        if resolution.selected is None:
            return PreparedMsiUninstall(resolution=resolution)
        if self._process_is_elevated():
            raise MsiUninstallExecutionError(
                MsiUninstallExecutionErrorCode.ELEVATED_PROCESS_BLOCKED,
                "Stage 4D2A refuses to run from an elevated Agent process",
            )
        target, raw = self._fresh_target(resolution.selected.identity.canonical_digest(), token)
        capability = self._capability.resolve(target, raw)
        product = self._product_validator.validate(target, capability)
        analysis = self._analysis_policy.assess(target)
        assessment = self._execution_policy.assess(product, analysis)
        if assessment.decision is not MsiExecutionDecision.ALLOW:
            raise MsiUninstallExecutionError(
                MsiUninstallExecutionErrorCode.POLICY_BLOCKED,
                assessment.reasons[0],
            )
        preflight = self._preflight.inspect(target, product, token)
        plan = MsiUninstallPlan(
            user_goal=user_goal,
            target_query=query,
            identity_digest=target.identity.canonical_digest(),
            validated_product_digest=product.evidence_digest(),
            capability_digest=capability.canonical_digest(),
            execution_assessment_digest=assessment.canonical_digest(),
            preflight_digest=preflight.canonical_digest(),
            risk_level=assessment.risk_level,
            max_items=self._max_items,
        )
        preview = self._preview_engine.build(
            plan,
            target,
            product,
            capability,
            assessment,
            preflight,
        )
        review = self._safety_validator.review(plan, preview)
        self._audit.previewed(plan, preview)
        if not review.approved:
            return PreparedMsiUninstall(
                resolution=resolution,
                plan=plan,
                preview=preview,
                review=review,
            )
        self._repository.create(plan, preview)
        try:
            confirmation = self._confirmations.request_plan(plan, preview)
        except Exception:
            self._repository.transition(
                plan.transaction_id,
                MsiUninstallTransactionState.BLOCKED,
                error_code=MsiUninstallExecutionErrorCode.EXECUTION_FAILED.value,
                error_message="Plan confirmation could not be durably created",
            )
            raise
        return PreparedMsiUninstall(
            resolution=resolution,
            plan=plan,
            preview=preview,
            review=review,
            plan_confirmation=confirmation,
        )

    def resolve_plan_confirmation(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: MsiUninstallPlan,
        preview: MsiUninstallPreview,
    ) -> MsiUninstallConfirmation:
        """Resolve and audit the first exact approval."""
        confirmation = self._confirmations.resolve_plan(
            confirmation_id,
            approved,
            plan,
            preview,
        )
        self._audit.confirmation_resolved(plan, confirmation)
        return confirmation

    def prepare_runtime_confirmation(
        self,
        plan_confirmation_id: UUID,
        plan: MsiUninstallPlan,
        cancellation: CancellationToken | None = None,
    ) -> PreparedMsiRuntimeConfirmation:
        """Rebuild all evidence and issue a new short-lived immediate gate."""
        self._repository.transition(
            plan.transaction_id,
            MsiUninstallTransactionState.VALIDATING,
        )
        token = cancellation or CancellationToken()
        try:
            if self._process_is_elevated():
                raise MsiUninstallExecutionError(
                    MsiUninstallExecutionErrorCode.ELEVATED_PROCESS_BLOCKED,
                    "Agent elevation changed after plan confirmation",
                )
            target, raw = self._fresh_target(plan.identity_digest, token)
            capability = self._capability.resolve(target, raw)
            product = self._product_validator.validate(target, capability)
            analysis = self._analysis_policy.assess(target)
            assessment = self._execution_policy.assess(product, analysis)
            if assessment.decision is not MsiExecutionDecision.ALLOW:
                raise MsiUninstallExecutionError(
                    MsiUninstallExecutionErrorCode.POLICY_BLOCKED,
                    assessment.reasons[0],
                )
            preflight = self._preflight.inspect(target, product, token)
            preview = self._preview_engine.build(
                plan,
                target,
                product,
                capability,
                assessment,
                preflight,
            )
            review = self._safety_validator.review(plan, preview)
            if not review.approved:
                raise MsiUninstallExecutionError(
                    MsiUninstallExecutionErrorCode.SAFETY_REVIEW_FAILED,
                    "; ".join(review.issues),
                )
            confirmation = self._confirmations.request_runtime(
                plan_confirmation_id,
                plan,
                preview,
            )
            return PreparedMsiRuntimeConfirmation(preview, confirmation)
        except Exception as exc:
            self._repository.transition(
                plan.transaction_id,
                MsiUninstallTransactionState.BLOCKED,
                error_code=(
                    exc.code.value
                    if isinstance(exc, MsiUninstallExecutionError)
                    else MsiUninstallExecutionErrorCode.TARGET_CHANGED.value
                ),
                error_message="Fresh MSI execution evidence did not reproduce the approved plan",
            )
            raise

    def resolve_runtime_confirmation(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: MsiUninstallPlan,
        preview: MsiUninstallPreview,
    ) -> MsiUninstallConfirmation:
        """Resolve and audit the immediate confirmation."""
        confirmation = self._confirmations.resolve_runtime(
            confirmation_id,
            approved,
            plan,
            preview,
        )
        self._audit.confirmation_resolved(plan, confirmation)
        return confirmation

    def execute(
        self,
        runtime_confirmation_id: UUID,
        plan: MsiUninstallPlan,
        preview: MsiUninstallPreview,
        cancellation: CancellationToken | None = None,
    ) -> MsiUninstallExecutionReport:
        """Consume authorization, dispatch once, then verify and audit without retry."""
        token = cancellation or CancellationToken()
        self._confirmations.consume_runtime(runtime_confirmation_id, plan, preview)
        try:
            self._audit.started(plan, preview, str(runtime_confirmation_id))
        except AuditUnavailableError:
            self._repository.transition(
                plan.transaction_id,
                MsiUninstallTransactionState.FAILED,
                error_code="audit_unavailable",
                error_message="Mandatory write-ahead audit failed; MSI was not launched",
            )
            raise
        request = MsiUninstallRequest(
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            product=preview.validated_product,
        )
        request_arguments = request.model_dump(mode="json")
        authorization = ExecutionAuthorization(
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            tool_name=plan.tool_name,
            arguments_digest=arguments_digest(request_arguments),
            runtime_confirmation_id=runtime_confirmation_id,
        )
        try:
            result = self._registry.execute(
                plan.tool_name,
                request_arguments,
                token,
                authorization,
            )
            if not isinstance(result, MsiUninstallResult):
                raise TypeError("MSI uninstall registry returned an unexpected result")
        except Exception as exc:
            state = self._repository.state(plan.transaction_id)
            if state in {
                MsiUninstallTransactionState.DISPATCHING,
                MsiUninstallTransactionState.EXECUTING,
            }:
                self._repository.transition(
                    plan.transaction_id,
                    MsiUninstallTransactionState.FAILED,
                    error_code=MsiUninstallExecutionErrorCode.EXECUTION_FAILED.value,
                    error_message="MSI adapter failed; current product state requires inspection",
                )
            self._audit.failed(
                plan,
                phase="adapter",
                error_code=type(exc).__name__,
                mutation_may_have_started=state is MsiUninstallTransactionState.EXECUTING,
            )
            raise
        if result.installer.category is MsiInstallerResultCategory.MONITORING_DETACHED:
            self._repository.transition(
                plan.transaction_id,
                MsiUninstallTransactionState.WAITING,
                installer_result={
                    "category": result.installer.category.value,
                    "exit_code": None,
                    "launched": True,
                },
            )
            report = MsiUninstallExecutionReport(
                transaction_id=plan.transaction_id,
                plan_id=plan.plan_id,
                preview_id=preview.preview_id,
                target_summary=(
                    f"{preview.target.display_name} {preview.target.display_version or ''}"
                ).strip(),
                risk_level=plan.risk_level,
                installer=result.installer,
                verification=MsiUninstallVerification(
                    state=MsiVerificationState.INTERRUPTED,
                    original_identity_present=None,
                    original_product_code_present=None,
                    inventory_refreshed=False,
                    evidence=("The fixed monitoring window elapsed before MSI exited.",),
                    warnings=(
                        "Windows Installer may still be running. The Agent did not terminate it "
                        "and did not perform a premature final verification.",
                    ),
                ),
                residual=MsiResidualReport(
                    checked_location=False,
                    warnings=(
                        "Residual inspection was deferred while Windows Installer may be active.",
                    ),
                ),
                recovery_guidance=preview.recovery_guidance,
            )
            try:
                self._audit.completed(plan, report)
            except AuditUnavailableError:
                report = report.model_copy(
                    update={
                        "verification": report.verification.model_copy(
                            update={
                                "warnings": (
                                    *report.verification.warnings,
                                    "MSI monitoring detached, and final audit persistence failed; "
                                    "the action will not be retried.",
                                )
                            }
                        )
                    }
                )
            return report
        self._repository.transition(
            plan.transaction_id,
            MsiUninstallTransactionState.INSTALLER_COMPLETED,
            installer_result={
                "category": result.installer.category.value,
                "exit_code": result.installer.exit_code,
                "launched": result.installer.launched,
            },
        )
        self._repository.transition(
            plan.transaction_id,
            MsiUninstallTransactionState.VERIFYING,
        )
        verification = self._verifier.verify(
            preview.validated_product,
            result.installer,
            plan.max_items,
            CancellationToken(),
        )
        residual = self._residual.analyze(preview.target.install_location)
        report = MsiUninstallExecutionReport(
            transaction_id=plan.transaction_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            target_summary=(
                f"{preview.target.display_name} {preview.target.display_version or ''}".strip()
            ),
            risk_level=plan.risk_level,
            installer=result.installer,
            verification=verification,
            residual=residual,
            recovery_guidance=preview.recovery_guidance,
        )
        terminal = _terminal_state(result.installer.category, verification.state)
        self._repository.transition(
            plan.transaction_id,
            terminal,
            verification_result={
                "state": verification.state.value,
                "identity_present": verification.original_identity_present,
                "product_code_present": verification.original_product_code_present,
                "replacement_candidates": verification.replacement_candidates,
            },
        )
        try:
            self._audit.completed(plan, report)
        except AuditUnavailableError:
            report = report.model_copy(
                update={
                    "verification": verification.model_copy(
                        update={
                            "warnings": (
                                *verification.warnings,
                                "Uninstall completed, but final audit persistence failed; "
                                "the action will not be retried.",
                            )
                        }
                    )
                }
            )
        return report

    def _fresh_target(
        self,
        identity_digest: str,
        cancellation: CancellationToken,
    ) -> tuple[NormalizedInstalledSoftware, RawInstalledSoftwareEntry]:
        target, snapshot = self._resolver.inspect(
            identity_digest,
            self._max_items,
            cancellation,
        )
        if snapshot.inventory.truncated or snapshot.inventory.warnings:
            raise MsiUninstallExecutionError(
                MsiUninstallExecutionErrorCode.TARGET_CHANGED,
                "Software inventory evidence is incomplete; execution is blocked",
            )
        if target is None:
            raise MsiUninstallExecutionError(
                MsiUninstallExecutionErrorCode.TARGET_CHANGED,
                "Software identity disappeared or changed",
            )
        raw = snapshot.raw_by_identity.get(identity_digest)
        if raw is None:
            raise MsiUninstallExecutionError(
                MsiUninstallExecutionErrorCode.RAW_EVIDENCE_MISSING,
                "Local MSI source evidence no longer matches the identity",
            )
        return target, raw


def _terminal_state(
    installer: MsiInstallerResultCategory,
    verification: MsiVerificationState,
) -> MsiUninstallTransactionState:
    if installer is MsiInstallerResultCategory.SUCCESS_REBOOT_REQUIRED:
        return MsiUninstallTransactionState.REBOOT_REQUIRED
    if installer is MsiInstallerResultCategory.USER_CANCELLED:
        return MsiUninstallTransactionState.USER_CANCELLED
    if installer is MsiInstallerResultCategory.PRIVILEGE_REQUIRED:
        return MsiUninstallTransactionState.PRIVILEGE_REQUIRED
    if verification is MsiVerificationState.VERIFIED_REMOVED:
        return MsiUninstallTransactionState.VERIFIED_REMOVED
    if verification in {
        MsiVerificationState.COMPLETED_UNVERIFIED,
        MsiVerificationState.REMOVED_WITH_UNEXPECTED_INSTALLER_RESULT,
        MsiVerificationState.TARGET_REPLACED_OR_UPGRADED,
    }:
        return MsiUninstallTransactionState.COMPLETED_UNVERIFIED
    return MsiUninstallTransactionState.FAILED
