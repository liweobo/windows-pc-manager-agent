"""Deterministic Stage 4D2B interactive Vendor uninstall orchestration."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from pc_manager_agent.audit.repository import AuditUnavailableError
from pc_manager_agent.audit.vendor_uninstall import VendorUninstallAuditLogger
from pc_manager_agent.confirmation.vendor_uninstall import (
    VendorUninstallConfirmation,
    VendorUninstallConfirmationService,
)
from pc_manager_agent.domain.software_uninstall_analysis import (
    CapabilitySupport,
    NormalizedInstalledSoftware,
    RawInstalledSoftwareEntry,
    ResolvedSoftwareTarget,
    SoftwareTargetQuery,
    UninstallCapability,
    UninstallCapabilityType,
)
from pc_manager_agent.domain.vendor_uninstall import (
    ValidatedVendorUninstallAction,
    VendorExecutionAssessment,
    VendorExecutionDecision,
    VendorExecutionPreflightResult,
    VendorProcessResultCategory,
    VendorResidualReport,
    VendorUninstallerIdentity,
    VendorUninstallExecutionReport,
    VendorUninstallPlan,
    VendorUninstallPreview,
    VendorUninstallRequest,
    VendorUninstallResult,
    VendorUninstallTransactionState,
    VendorUninstallVerification,
    VendorVerificationState,
)
from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.orchestration.uninstall_context import UninstallContextRecorder
from pc_manager_agent.orchestration.vendor_execution_preflight import VendorExecutionPreflight
from pc_manager_agent.orchestration.vendor_residual_analyzer import VendorResidualAnalyzer
from pc_manager_agent.orchestration.vendor_uninstall_metadata import VendorUninstallMetadataParser
from pc_manager_agent.orchestration.vendor_uninstall_verifier import VendorUninstallVerifier
from pc_manager_agent.persistence.vendor_uninstall import VendorUninstallRepository
from pc_manager_agent.safety.software_uninstall_policy import SoftwareUninstallSafetyPolicy
from pc_manager_agent.safety.vendor_argument_policy import VendorArgumentPolicy
from pc_manager_agent.safety.vendor_executable_trust import VendorExecutableTrustValidator
from pc_manager_agent.safety.vendor_uninstall_policy import VendorUninstallExecutionPolicy
from pc_manager_agent.safety.vendor_uninstall_preview import VendorUninstallPreviewEngine
from pc_manager_agent.safety.vendor_uninstall_validator import (
    VendorUninstallSafetyReview,
    VendorUninstallSafetyValidator,
)
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


class VendorUninstallExecutionErrorCode(StrEnum):
    """Fail-closed Vendor orchestration reasons safe to display and audit."""

    TARGET_CHANGED = "target_changed"
    RAW_EVIDENCE_MISSING = "raw_evidence_missing"
    INTERACTIVE_METADATA_REQUIRED = "interactive_metadata_required"
    CAPABILITY_UNSUPPORTED = "capability_unsupported"
    POLICY_BLOCKED = "policy_blocked"
    ELEVATED_PROCESS_BLOCKED = "elevated_process_blocked"
    SAFETY_REVIEW_FAILED = "safety_review_failed"
    EXECUTION_FAILED = "execution_failed"


class VendorUninstallExecutionError(RuntimeError):
    """Raised without exposing raw command metadata, paths, arguments, or environment."""

    def __init__(self, code: VendorUninstallExecutionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PreparedVendorUninstall:
    """Preparation outcome containing candidates or one reviewed Vendor Preview."""

    resolution: ResolvedSoftwareTarget
    plan: VendorUninstallPlan | None = None
    preview: VendorUninstallPreview | None = None
    review: VendorUninstallSafetyReview | None = None
    plan_confirmation: VendorUninstallConfirmation | None = None


@dataclass(frozen=True, slots=True)
class PreparedVendorRuntimeConfirmation:
    """Fresh pre-dispatch Preview and its immediate confirmation."""

    preview: VendorUninstallPreview
    confirmation: VendorUninstallConfirmation


class VendorUninstallService:
    """Coordinate identity, trust, confirmations, one tool, verification, and audit."""

    def __init__(
        self,
        resolver: SoftwareTargetResolver,
        capability: UninstallCapabilityResolver,
        metadata: VendorUninstallMetadataParser,
        arguments: VendorArgumentPolicy,
        executable_trust: VendorExecutableTrustValidator,
        analysis_policy: SoftwareUninstallSafetyPolicy,
        execution_policy: VendorUninstallExecutionPolicy,
        preflight: VendorExecutionPreflight,
        preview_engine: VendorUninstallPreviewEngine,
        safety_validator: VendorUninstallSafetyValidator,
        confirmations: VendorUninstallConfirmationService,
        repository: VendorUninstallRepository,
        registry: ToolRegistry,
        verifier: VendorUninstallVerifier,
        residual: VendorResidualAnalyzer,
        audit: VendorUninstallAuditLogger,
        *,
        process_is_elevated: Callable[[], bool],
        max_items: int = 5_000,
        context_recorder: UninstallContextRecorder | None = None,
    ) -> None:
        self._resolver = resolver
        self._capability = capability
        self._metadata = metadata
        self._arguments = arguments
        self._executable_trust = executable_trust
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
        self._context_recorder = context_recorder

    def prepare(
        self,
        user_goal: str,
        query: SoftwareTargetQuery,
        cancellation: CancellationToken | None = None,
    ) -> PreparedVendorUninstall:
        """Refresh all local evidence and request only the first confirmation."""
        token = cancellation or CancellationToken()
        resolution, _snapshot = self._resolver.resolve(query, self._max_items, token)
        if resolution.selected is None:
            return PreparedVendorUninstall(resolution=resolution)
        if self._process_is_elevated():
            raise VendorUninstallExecutionError(
                VendorUninstallExecutionErrorCode.ELEVATED_PROCESS_BLOCKED,
                "Stage 4D2B refuses to run from an elevated Agent process",
            )
        target, raw = self._fresh_target(resolution.selected.identity.canonical_digest(), token)
        capability, identity, assessment, preflight = self._build_evidence(
            target,
            raw,
            token,
            active_uninstall_present=self._repository.has_active_uninstall(),
        )
        if assessment.decision is not VendorExecutionDecision.ALLOW:
            raise VendorUninstallExecutionError(
                VendorUninstallExecutionErrorCode.POLICY_BLOCKED,
                assessment.reasons[0],
            )
        plan = VendorUninstallPlan(
            user_goal=user_goal,
            target_query=query,
            identity_digest=target.identity.canonical_digest(),
            capability_digest=capability.canonical_digest(),
            vendor_identity_digest=identity.invariant_digest(),
            execution_assessment_digest=assessment.canonical_digest(),
            preflight_digest=preflight.canonical_digest(),
            risk_level=assessment.risk_level,
            max_items=self._max_items,
        )
        preview = self._preview_engine.build(
            plan,
            target,
            capability,
            identity,
            assessment,
            preflight,
        )
        review = self._safety_validator.review(plan, preview)
        self._audit.previewed(plan, preview)
        if not review.approved:
            return PreparedVendorUninstall(
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
                VendorUninstallTransactionState.BLOCKED,
                error_code=VendorUninstallExecutionErrorCode.EXECUTION_FAILED.value,
                error_message="Plan confirmation could not be durably created",
            )
            raise
        return PreparedVendorUninstall(
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
        plan: VendorUninstallPlan,
        preview: VendorUninstallPreview,
    ) -> VendorUninstallConfirmation:
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
        plan: VendorUninstallPlan,
        cancellation: CancellationToken | None = None,
    ) -> PreparedVendorRuntimeConfirmation:
        """Rebuild every execution input and issue the immediate object-specific gate."""
        self._repository.transition(
            plan.transaction_id,
            VendorUninstallTransactionState.VALIDATING,
        )
        token = cancellation or CancellationToken()
        try:
            if self._process_is_elevated():
                raise VendorUninstallExecutionError(
                    VendorUninstallExecutionErrorCode.ELEVATED_PROCESS_BLOCKED,
                    "Agent elevation changed after plan confirmation",
                )
            target, raw = self._fresh_target(plan.identity_digest, token)
            capability, identity, assessment, preflight = self._build_evidence(
                target,
                raw,
                token,
                active_uninstall_present=self._repository.has_active_uninstall(
                    exclude_vendor_transaction=plan.transaction_id
                ),
            )
            preview = self._preview_engine.build(
                plan,
                target,
                capability,
                identity,
                assessment,
                preflight,
            )
            review = self._safety_validator.review(plan, preview)
            if not review.approved:
                raise VendorUninstallExecutionError(
                    VendorUninstallExecutionErrorCode.SAFETY_REVIEW_FAILED,
                    "; ".join(review.issues),
                )
            confirmation = self._confirmations.request_runtime(
                plan_confirmation_id,
                plan,
                preview,
            )
            return PreparedVendorRuntimeConfirmation(preview, confirmation)
        except Exception as exc:
            self._repository.transition(
                plan.transaction_id,
                VendorUninstallTransactionState.BLOCKED,
                error_code=(
                    exc.code.value
                    if isinstance(exc, VendorUninstallExecutionError)
                    else VendorUninstallExecutionErrorCode.TARGET_CHANGED.value
                ),
                error_message="Fresh Vendor evidence did not reproduce the approved plan",
            )
            raise

    def resolve_runtime_confirmation(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: VendorUninstallPlan,
        preview: VendorUninstallPreview,
    ) -> VendorUninstallConfirmation:
        """Resolve and audit the short-lived immediate confirmation."""
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
        plan: VendorUninstallPlan,
        preview: VendorUninstallPreview,
        cancellation: CancellationToken | None = None,
    ) -> VendorUninstallExecutionReport:
        """Consume authorization, dispatch once, and verify without retry or residual deletion."""
        token = cancellation or CancellationToken()
        self._confirmations.consume_runtime(runtime_confirmation_id, plan, preview)
        try:
            self._audit.started(plan, preview, str(runtime_confirmation_id))
        except AuditUnavailableError:
            self._repository.transition(
                plan.transaction_id,
                VendorUninstallTransactionState.FAILED,
                error_code="audit_unavailable",
                error_message="Mandatory audit failed; Vendor uninstaller was not launched",
            )
            raise
        request = VendorUninstallRequest(
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            action=ValidatedVendorUninstallAction(
                software_identity_hash=preview.identity_digest,
                vendor_identity=preview.vendor_identity,
                transaction_id=plan.transaction_id,
                validated_at=preview.generated_at,
            ),
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
        context_id = (
            self._context_recorder.capture_vendor(preview)
            if self._context_recorder is not None
            else None
        )
        try:
            result = self._registry.execute(
                plan.tool_name,
                request_arguments,
                token,
                authorization,
            )
            if not isinstance(result, VendorUninstallResult):
                raise TypeError("Vendor registry returned an unexpected result")
        except Exception as exc:
            state = self._repository.state(plan.transaction_id)
            if state in {
                VendorUninstallTransactionState.DISPATCHING,
                VendorUninstallTransactionState.EXECUTING,
            }:
                self._repository.transition(
                    plan.transaction_id,
                    VendorUninstallTransactionState.FAILED,
                    error_code=VendorUninstallExecutionErrorCode.EXECUTION_FAILED.value,
                    error_message="Vendor adapter failed; current software state needs inspection",
                )
            self._audit.failed(
                plan,
                phase="adapter",
                error_code=type(exc).__name__,
                mutation_may_have_started=state is VendorUninstallTransactionState.EXECUTING,
            )
            if self._context_recorder is not None:
                self._context_recorder.finalize(
                    context_id,
                    verification_state="failed",
                    verified_removed=False,
                )
            raise
        if result.process.category in {
            VendorProcessResultCategory.MONITORING_DETACHED,
            VendorProcessResultCategory.STOPPED_MONITORING,
        }:
            if self._context_recorder is not None:
                self._context_recorder.finalize(
                    context_id,
                    verification_state=VendorVerificationState.INTERRUPTED.value,
                    verified_removed=False,
                    completed_at=result.process.finished_at,
                )
            return self._monitoring_stopped_report(plan, preview, result)
        self._repository.transition(
            plan.transaction_id,
            VendorUninstallTransactionState.PROCESS_EXITED,
            process_result={
                "category": result.process.category.value,
                "exit_code": result.process.exit_code,
                "launched": result.process.launched,
                "tracked_child_count": result.process.tracked_child_count,
            },
        )
        self._repository.transition(
            plan.transaction_id,
            VendorUninstallTransactionState.VERIFYING,
        )
        verification = self._verifier.verify(
            preview.target,
            result.process,
            plan.max_items,
            CancellationToken(),
        )
        residual = self._residual.analyze(preview.target.install_location)
        report = self._report(plan, preview, result, verification, residual)
        self._repository.transition(
            plan.transaction_id,
            _terminal_state(verification.state),
            verification_result={
                "state": verification.state.value,
                "identity_present": verification.original_identity_present,
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
                                "Final audit persistence failed; the action will not be retried.",
                            )
                        }
                    )
                }
            )
        if self._context_recorder is not None:
            self._context_recorder.finalize(
                context_id,
                verification_state=verification.state.value,
                verified_removed=verification.state
                in {
                    VendorVerificationState.VERIFIED_REMOVED,
                    VendorVerificationState.REMOVED_WITH_UNEXPECTED_PROCESS_RESULT,
                },
                completed_unverified=(
                    verification.state is VendorVerificationState.COMPLETED_UNVERIFIED
                ),
                completed_at=result.process.finished_at,
            )
        return report

    def _build_evidence(
        self,
        target: NormalizedInstalledSoftware,
        raw: RawInstalledSoftwareEntry,
        cancellation: CancellationToken,
        *,
        active_uninstall_present: bool,
    ) -> tuple[
        UninstallCapability,
        VendorUninstallerIdentity,
        VendorExecutionAssessment,
        VendorExecutionPreflightResult,
    ]:
        """Build one complete evidence set while keeping raw metadata ephemeral."""
        interactive_raw = raw.model_copy(update={"quiet_uninstall_string": None})
        capability = self._capability.resolve(target, interactive_raw)
        if (
            capability.capability_type is not UninstallCapabilityType.VENDOR_UNINSTALLER
            or capability.support is not CapabilitySupport.METADATA_SUPPORTED
        ):
            raise VendorUninstallExecutionError(
                VendorUninstallExecutionErrorCode.CAPABILITY_UNSUPPORTED,
                "Interactive Vendor uninstall metadata is unsupported",
            )
        parsed = self._metadata.parse(raw)
        argument_assessment = self._arguments.assess(parsed.raw_arguments)
        identity = self._executable_trust.build_identity(
            target,
            raw,
            parsed,
            argument_assessment,
        )
        analysis = self._analysis_policy.assess(target)
        assessment = self._execution_policy.assess(target.scope, analysis, identity)
        preflight = self._preflight.inspect(
            target,
            identity,
            cancellation,
            active_uninstall_present=active_uninstall_present,
        )
        return capability, identity, assessment, preflight

    def _fresh_target(
        self,
        identity_digest: str,
        cancellation: CancellationToken,
    ) -> tuple[NormalizedInstalledSoftware, RawInstalledSoftwareEntry]:
        """Require a complete exact current inventory and its matching raw record."""
        target, snapshot = self._resolver.inspect(
            identity_digest,
            self._max_items,
            cancellation,
        )
        if snapshot.inventory.truncated or snapshot.inventory.warnings:
            raise VendorUninstallExecutionError(
                VendorUninstallExecutionErrorCode.TARGET_CHANGED,
                "Software inventory evidence is incomplete; execution is blocked",
            )
        if target is None:
            raise VendorUninstallExecutionError(
                VendorUninstallExecutionErrorCode.TARGET_CHANGED,
                "Software identity disappeared or changed",
            )
        raw = snapshot.raw_by_identity.get(identity_digest)
        if raw is None:
            raise VendorUninstallExecutionError(
                VendorUninstallExecutionErrorCode.RAW_EVIDENCE_MISSING,
                "Local Vendor source evidence no longer matches the identity",
            )
        if raw.uninstall_string is None:
            raise VendorUninstallExecutionError(
                VendorUninstallExecutionErrorCode.INTERACTIVE_METADATA_REQUIRED,
                "Only an interactive UninstallString may enter Stage 4D2B",
            )
        return target, raw

    def _monitoring_stopped_report(
        self,
        plan: VendorUninstallPlan,
        preview: VendorUninstallPreview,
        result: VendorUninstallResult,
    ) -> VendorUninstallExecutionReport:
        """Report an unknown final state without killing, retrying, or verifying prematurely."""
        self._repository.transition(
            plan.transaction_id,
            VendorUninstallTransactionState.MONITORING,
            process_result={
                "category": result.process.category.value,
                "launched": True,
                "process_id": result.process.process_id,
                "tracked_child_count": result.process.tracked_child_count,
            },
        )
        report = self._report(
            plan,
            preview,
            result,
            VendorUninstallVerification(
                state=VendorVerificationState.INTERRUPTED,
                original_identity_present=None,
                inventory_refreshed=False,
                evidence=("Monitoring ended while Vendor processes may still be active.",),
                warnings=(
                    "The Agent did not terminate any process and did not perform premature final "
                    "verification. Refresh installed software after the Vendor UI finishes.",
                ),
            ),
            VendorResidualReport(
                checked_location=False,
                warnings=("Residual inspection was deferred while execution may be active.",),
            ),
        )
        with suppress(AuditUnavailableError):
            self._audit.completed(plan, report)
        return report

    @staticmethod
    def _report(
        plan: VendorUninstallPlan,
        preview: VendorUninstallPreview,
        result: VendorUninstallResult,
        verification: VendorUninstallVerification,
        residual: VendorResidualReport,
    ) -> VendorUninstallExecutionReport:
        """Build one user report that never equates process exit with uninstall success."""
        return VendorUninstallExecutionReport(
            transaction_id=plan.transaction_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            target_summary=(
                f"{preview.target.display_name} {preview.target.display_version or ''}".strip()
            ),
            risk_level=plan.risk_level,
            process=result.process,
            verification=verification,
            residual=residual,
            recovery_guidance=preview.recovery_guidance,
        )


def _terminal_state(state: VendorVerificationState) -> VendorUninstallTransactionState:
    """Map observed verification to one durable terminal state without retry."""
    if state is VendorVerificationState.VERIFIED_REMOVED:
        return VendorUninstallTransactionState.VERIFIED_REMOVED
    if state in {
        VendorVerificationState.COMPLETED_UNVERIFIED,
        VendorVerificationState.REMOVED_WITH_UNEXPECTED_PROCESS_RESULT,
        VendorVerificationState.TARGET_INSTANCE_CHANGED,
    }:
        return VendorUninstallTransactionState.COMPLETED_UNVERIFIED
    return VendorUninstallTransactionState.FAILED
