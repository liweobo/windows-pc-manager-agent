"""Plan-confirm-revalidate-execute-verify workflow for one winget package."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from uuid import UUID

from pc_manager_agent.audit.repository import AuditUnavailableError
from pc_manager_agent.audit.winget_uninstall import WingetUninstallAuditLogger
from pc_manager_agent.confirmation.winget_uninstall import (
    WingetUninstallConfirmation,
    WingetUninstallConfirmationService,
)
from pc_manager_agent.domain.software_uninstall_analysis import (
    NormalizedInstalledSoftware,
    SoftwareTargetQuery,
)
from pc_manager_agent.domain.winget_uninstall import (
    NormalizedWingetPackage,
    ValidatedWingetUninstallAction,
    WingetAvailability,
    WingetCapabilityAssessment,
    WingetExecutionAssessment,
    WingetExecutionPreflight,
    WingetProcessResultCategory,
    WingetResidualReport,
    WingetSoftwareMapping,
    WingetUninstallExecutionReport,
    WingetUninstallPlan,
    WingetUninstallPreview,
    WingetUninstallRequest,
    WingetUninstallResult,
    WingetUninstallTransactionState,
    WingetUninstallVerification,
    WingetVerificationState,
)
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.orchestration.winget_execution_preflight import (
    WingetExecutionPreflightService,
)
from pc_manager_agent.orchestration.winget_inventory import WingetAvailabilityService
from pc_manager_agent.orchestration.winget_residual_analyzer import WingetResidualAnalyzer
from pc_manager_agent.orchestration.winget_software_mapping import WingetSoftwareMapper
from pc_manager_agent.orchestration.winget_target_resolver import PackageTargetResolver
from pc_manager_agent.orchestration.winget_uninstall_verifier import WingetUninstallVerifier
from pc_manager_agent.persistence.winget_uninstall import (
    WingetUninstallRepository,
    WingetUninstallStoreError,
)
from pc_manager_agent.safety.software_uninstall_policy import SoftwareUninstallSafetyPolicy
from pc_manager_agent.safety.winget_capability_policy import WingetCapabilityPolicy
from pc_manager_agent.safety.winget_uninstall_policy import WingetUninstallPolicy
from pc_manager_agent.safety.winget_uninstall_preview import WingetUninstallPreviewEngine
from pc_manager_agent.safety.winget_uninstall_validator import WingetUninstallSafetyValidator
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


class WingetUninstallExecutionError(RuntimeError):
    """Raised when any fail-closed Stage 4D2C1 gate rejects execution."""


@dataclass(frozen=True, slots=True)
class PreparedWingetUninstall:
    """Preparation result containing candidates or one confirmed-target Preview."""

    plan: WingetUninstallPlan | None
    preview: WingetUninstallPreview | None
    plan_confirmation: WingetUninstallConfirmation | None
    package_candidates: tuple[NormalizedWingetPackage, ...] = ()


@dataclass(frozen=True, slots=True)
class PreparedWingetRuntimeConfirmation:
    """Fresh Preview and immediate confirmation returned after revalidation."""

    preview: WingetUninstallPreview
    confirmation: WingetUninstallConfirmation


class WingetUninstallService:
    """Coordinate deterministic evidence while delegating only fixed tool execution."""

    def __init__(
        self,
        *,
        package_resolver: PackageTargetResolver,
        software_resolver: SoftwareTargetResolver,
        mapper: WingetSoftwareMapper,
        availability: WingetAvailabilityService,
        capability: WingetCapabilityPolicy,
        analysis_policy: SoftwareUninstallSafetyPolicy,
        execution_policy: WingetUninstallPolicy,
        preflight: WingetExecutionPreflightService,
        preview_engine: WingetUninstallPreviewEngine,
        validator: WingetUninstallSafetyValidator,
        repository: WingetUninstallRepository,
        confirmations: WingetUninstallConfirmationService,
        registry: ToolRegistry,
        verifier: WingetUninstallVerifier,
        residual: WingetResidualAnalyzer,
        audit: WingetUninstallAuditLogger,
        process_is_elevated: Callable[[], bool],
        max_items: int = 5_000,
    ) -> None:
        self._package_resolver = package_resolver
        self._software_resolver = software_resolver
        self._mapper = mapper
        self._availability = availability
        self._capability = capability
        self._analysis_policy = analysis_policy
        self._execution_policy = execution_policy
        self._preflight = preflight
        self._preview_engine = preview_engine
        self._validator = validator
        self._repository = repository
        self._confirmations = confirmations
        self._registry = registry
        self._verifier = verifier
        self._residual = residual
        self._audit = audit
        self._max_items = max_items
        self._process_is_elevated = process_is_elevated

    def prepare(
        self,
        user_goal: str,
        software_query: SoftwareTargetQuery,
        cancellation: CancellationToken | None = None,
    ) -> PreparedWingetUninstall:
        """Refresh a software target and prepare one digest-bound package Preview."""
        token = cancellation or CancellationToken()
        if self._process_is_elevated():
            raise WingetUninstallExecutionError(
                "Stage 4D2C1 is disabled while the Agent process is elevated"
            )
        resolution, software_snapshot = self._software_resolver.resolve(
            software_query,
            self._max_items,
            token,
        )
        software = resolution.selected
        if software is None:
            raise WingetUninstallExecutionError("installed-software target is not unique")
        package_id = software.identity.package_id
        if package_id is None:
            raise WingetUninstallExecutionError("software has no structured winget Package ID")
        from pc_manager_agent.domain.winget_uninstall import WingetPackageQuery

        package_resolution, package_inventory = self._package_resolver.resolve(
            WingetPackageQuery(
                package_id=package_id,
                installed_version=software.display_version,
            ),
            self._max_items,
            token,
        )
        package = package_resolution.selected
        if package is None:
            return PreparedWingetUninstall(
                plan=None,
                preview=None,
                plan_confirmation=None,
                package_candidates=package_resolution.candidates,
            )
        if package_inventory.warnings or software_snapshot.inventory.warnings:
            raise WingetUninstallExecutionError("inventory evidence is incomplete")
        mapping, mapped = self._mapper.map(package, software_snapshot.inventory.entries)
        if (
            mapped is None
            or mapped.identity.canonical_digest() != software.identity.canonical_digest()
        ):
            raise WingetUninstallExecutionError(
                "Package-to-Software mapping is not high confidence"
            )
        evidence = self._build_evidence(
            package,
            mapped,
            mapping,
            token,
            active=self._repository.has_active_uninstall(),
        )
        availability, capability, assessment, preflight = evidence
        executable = availability.executable
        if executable is None:
            raise WingetUninstallExecutionError("trusted winget executable is unavailable")
        plan = WingetUninstallPlan(
            user_goal=user_goal,
            package_query=package_resolution.query,
            package_identity_digest=package.identity.canonical_digest(),
            software_identity_digest=mapped.identity.canonical_digest(),
            mapping_digest=mapping.canonical_digest(),
            capability_digest=capability.canonical_digest(),
            executable_identity_digest=executable.invariant_digest(),
            safety_digest=assessment.canonical_digest(),
            preflight_digest=preflight.canonical_digest(),
            risk_level=assessment.risk_level,
        )
        preview = self._preview_engine.build(
            plan,
            package,
            mapped,
            mapping,
            availability,
            capability,
            assessment,
            preflight,
        )
        if not preview.executable:
            raise WingetUninstallExecutionError("winget package is blocked by current evidence")
        self._repository.create(plan, preview)
        try:
            self._audit.previewed(plan, preview)
        except AuditUnavailableError:
            self._repository.transition(
                plan.transaction_id,
                WingetUninstallTransactionState.BLOCKED,
                error_code="audit_unavailable",
                error_message="Mandatory audit failed before confirmation.",
            )
            raise
        confirmation = self._confirmations.request_plan(plan, preview)
        return PreparedWingetUninstall(plan, preview, confirmation)

    def resolve_plan_confirmation(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: WingetUninstallPlan,
        preview: WingetUninstallPreview,
    ) -> WingetUninstallConfirmation:
        """Resolve and audit the first user confirmation."""
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
        plan: WingetUninstallPlan,
        cancellation: CancellationToken | None = None,
    ) -> PreparedWingetRuntimeConfirmation:
        """Rebuild every execution fact immediately before the second confirmation."""
        token = cancellation or CancellationToken()
        if self._process_is_elevated():
            self._block(plan, "elevated_process", "Elevated Agent process is not allowed.")
            raise WingetUninstallExecutionError(
                "Stage 4D2C1 is disabled while the Agent process is elevated"
            )
        self._repository.transition(
            plan.transaction_id,
            WingetUninstallTransactionState.VALIDATING,
        )
        package, package_inventory = self._package_resolver.inspect(
            plan.package_identity_digest,
            self._max_items,
            token,
        )
        software, software_snapshot = self._software_resolver.inspect(
            plan.software_identity_digest,
            self._max_items,
            token,
        )
        if (
            package is None
            or software is None
            or package_inventory.warnings
            or software_snapshot.inventory.warnings
        ):
            self._block(plan, "target_changed", "Fresh package/software identity changed.")
            raise WingetUninstallExecutionError("fresh package/software identity changed")
        mapping, mapped = self._mapper.map(package, software_snapshot.inventory.entries)
        if mapped is None or mapped.identity.canonical_digest() != plan.software_identity_digest:
            self._block(plan, "mapping_changed", "Fresh package/software mapping changed.")
            raise WingetUninstallExecutionError("fresh package/software mapping changed")
        availability, capability, assessment, preflight = self._build_evidence(
            package,
            software,
            mapping,
            token,
            active=self._repository.has_active_uninstall(plan.transaction_id),
        )
        try:
            fresh = self._preview_engine.build(
                plan,
                package,
                software,
                mapping,
                availability,
                capability,
                assessment,
                preflight,
            )
            parent = self._repository.get_confirmation(plan_confirmation_id)
            self._validator.validate_invariant(plan, parent.invariant_digest, fresh)
            # The plan confirmation stored the original invariant. Requesting runtime
            # confirmation below enforces equality and invalidates any changed evidence.
            confirmation = self._confirmations.request_runtime(
                plan_confirmation_id,
                plan,
                fresh,
            )
        except Exception:
            self._block(plan, "runtime_revalidation_failed", "Runtime evidence changed.")
            raise
        return PreparedWingetRuntimeConfirmation(fresh, confirmation)

    def resolve_runtime_confirmation(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: WingetUninstallPlan,
        preview: WingetUninstallPreview,
    ) -> WingetUninstallConfirmation:
        """Resolve and audit the immediate object-specific confirmation."""
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
        plan: WingetUninstallPlan,
        preview: WingetUninstallPreview,
        cancellation: CancellationToken | None = None,
    ) -> WingetUninstallExecutionReport:
        """Consume authorization, dispatch once, then perform independent dual verification."""
        token = cancellation or CancellationToken()
        self._confirmations.consume_runtime(runtime_confirmation_id, plan, preview)
        try:
            self._audit.started(plan, preview, str(runtime_confirmation_id))
        except AuditUnavailableError:
            self._repository.transition(
                plan.transaction_id,
                WingetUninstallTransactionState.FAILED,
                error_code="audit_unavailable",
                error_message="Mandatory audit failed; winget was not launched.",
            )
            raise
        executable = preview.availability.executable
        if executable is None:
            raise WingetUninstallExecutionError("trusted winget executable disappeared")
        request = WingetUninstallRequest(
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            action=ValidatedWingetUninstallAction(
                transaction_id=plan.transaction_id,
                package_identity=preview.package.identity,
                software_identity_digest=preview.software.identity.canonical_digest(),
                executable_identity=executable,
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
        try:
            result = self._registry.execute(
                plan.tool_name,
                request_arguments,
                token,
                authorization,
            )
            if not isinstance(result, WingetUninstallResult):
                raise TypeError("winget registry returned an unexpected result")
        except Exception as exc:
            state = self._repository.state(plan.transaction_id)
            if state in {
                WingetUninstallTransactionState.DISPATCHING,
                WingetUninstallTransactionState.EXECUTING,
            }:
                self._repository.transition(
                    plan.transaction_id,
                    WingetUninstallTransactionState.FAILED,
                    error_code="execution_failed",
                    error_message="winget adapter failed; current software state needs inspection.",
                )
            self._audit.failed(
                plan,
                phase="adapter",
                error_code=type(exc).__name__,
                mutation_may_have_started=state is WingetUninstallTransactionState.EXECUTING,
            )
            raise
        if result.process.category in {
            WingetProcessResultCategory.CANCELLED_BEFORE_LAUNCH,
            WingetProcessResultCategory.LAUNCH_FAILED,
        }:
            cancelled = (
                result.process.category is WingetProcessResultCategory.CANCELLED_BEFORE_LAUNCH
            )
            verification = WingetUninstallVerification(
                state=(
                    WingetVerificationState.INTERRUPTED
                    if cancelled
                    else WingetVerificationState.FAILED
                ),
                package_inventory_refreshed=False,
                software_inventory_refreshed=False,
                original_package_present=None,
                original_software_present=None,
                evidence=(
                    "Cancellation was observed before process launch."
                    if cancelled
                    else "The trusted winget process could not be launched.",
                ),
                warnings=("No uninstall process was started and no retry was attempted.",),
            )
            residual = WingetResidualReport(
                checked_location=False,
                warnings=("Residual inspection is unnecessary because launch did not occur.",),
            )
            self._repository.transition(
                plan.transaction_id,
                (
                    WingetUninstallTransactionState.CANCELLED
                    if cancelled
                    else WingetUninstallTransactionState.FAILED
                ),
                process_result={
                    "category": result.process.category.value,
                    "launched": False,
                },
            )
        elif result.process.category is WingetProcessResultCategory.MONITORING_STOPPED:
            verification = self._verifier.verify(
                preview.package,
                preview.software,
                result.process,
                self._max_items,
            )
            residual = WingetResidualReport(
                checked_location=False,
                warnings=("Residual inspection was deferred while execution may be active.",),
            )
            self._repository.transition(
                plan.transaction_id,
                WingetUninstallTransactionState.INTERRUPTED,
                process_result={"category": result.process.category.value, "launched": True},
            )
        else:
            self._repository.transition(
                plan.transaction_id,
                WingetUninstallTransactionState.PROCESS_EXITED,
                process_result={
                    "category": result.process.category.value,
                    "exit_code": result.process.exit_code,
                    "launched": result.process.launched,
                },
            )
            self._repository.transition(
                plan.transaction_id,
                WingetUninstallTransactionState.VERIFYING,
            )
            verification = self._verifier.verify(
                preview.package,
                preview.software,
                result.process,
                self._max_items,
            )
            residual = self._residual.analyze(preview.software.install_location)
            self._repository.transition(
                plan.transaction_id,
                _terminal_state(verification.state),
                verification_result={
                    "state": verification.state.value,
                    "package_present": verification.original_package_present,
                    "software_present": verification.original_software_present,
                },
            )
        report = WingetUninstallExecutionReport(
            transaction_id=plan.transaction_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            target_summary=(
                f"{preview.software.display_name} {preview.software.display_version or ''}"
            ).strip(),
            risk_level=plan.risk_level,
            process=result.process,
            verification=verification,
            residual=residual,
            recovery_guidance=preview.recovery_guidance,
        )
        with suppress(AuditUnavailableError):
            self._audit.completed(plan, report)
        return report

    def _build_evidence(
        self,
        package: NormalizedWingetPackage,
        software: NormalizedInstalledSoftware,
        mapping: WingetSoftwareMapping,
        cancellation: CancellationToken,
        *,
        active: bool,
    ) -> tuple[
        WingetAvailability,
        WingetCapabilityAssessment,
        WingetExecutionAssessment,
        WingetExecutionPreflight,
    ]:
        availability = self._availability.inspect()
        capability = self._capability.assess(package, mapping, availability)
        analysis = self._analysis_policy.assess(software)
        assessment = self._execution_policy.assess(software.scope, analysis)
        preflight = self._preflight.inspect(
            software,
            cancellation,
            another_uninstall_active=active,
        )
        return availability, capability, assessment, preflight

    def _block(self, plan: WingetUninstallPlan, code: str, message: str) -> None:
        with suppress(WingetUninstallStoreError):
            self._repository.transition(
                plan.transaction_id,
                WingetUninstallTransactionState.BLOCKED,
                error_code=code,
                error_message=message,
            )


def _terminal_state(state: WingetVerificationState) -> WingetUninstallTransactionState:
    if state is WingetVerificationState.VERIFIED_REMOVED:
        return WingetUninstallTransactionState.VERIFIED_REMOVED
    if state in {
        WingetVerificationState.PACKAGE_REMOVED_SOFTWARE_PRESENT,
        WingetVerificationState.SOFTWARE_REMOVED_PACKAGE_UNKNOWN,
        WingetVerificationState.TARGET_INSTANCE_CHANGED,
        WingetVerificationState.COMPLETED_UNVERIFIED,
        WingetVerificationState.PACKAGE_STILL_PRESENT,
    }:
        return WingetUninstallTransactionState.COMPLETED_UNVERIFIED
    if state is WingetVerificationState.INTERRUPTED:
        return WingetUninstallTransactionState.INTERRUPTED
    return WingetUninstallTransactionState.FAILED
