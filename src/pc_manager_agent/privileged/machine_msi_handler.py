"""Broker-side exact machine MSI uninstall, verification, and no-retry handler."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pc_manager_agent.domain.elevated_broker import MachineMsiResultEvidence
from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    MachineMsiUninstallPayload,
    PrivilegedActionRequest,
    PrivilegedActionType,
    canonical_model_digest,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiExecutionDecision,
    MsiInstallerResultCategory,
    MsiPreflightState,
    MsiVerificationState,
    SoftwareExecutionPreflightResult,
    ValidatedMsiProduct,
)
from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
from pc_manager_agent.orchestration.software_execution_preflight import (
    SoftwareExecutionPreflight,
)
from pc_manager_agent.orchestration.software_msi_validation import MsiProductValidator
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.orchestration.software_uninstall_verifier import MsiUninstallVerifier
from pc_manager_agent.persistence.privileged_actions import PrivilegedActionRepository
from pc_manager_agent.persistence.software_uninstall_execution import (
    MsiUninstallRepository,
    MsiUninstallStoreError,
)
from pc_manager_agent.platform_support.windows.msi_uninstall import WindowsMsiUninstallPlatform
from pc_manager_agent.privileged.dispatcher import (
    FreshPrivilegedEvidence,
    PrivilegedHandlerOutcome,
)
from pc_manager_agent.privileged.revalidation import PrivilegedRevalidationError
from pc_manager_agent.safety.machine_msi_policy import MachineMsiExecutionPolicy
from pc_manager_agent.safety.software_uninstall_policy import SoftwareUninstallSafetyPolicy
from pc_manager_agent.tools.manifest import CancellationToken


@dataclass(frozen=True, slots=True)
class ValidatedMachineMsiRequest:
    """Fresh machine product and preflight evidence accepted inside the Broker."""

    product: ValidatedMsiProduct
    preflight: SoftwareExecutionPreflightResult


class WindowsMachineMsiPrivilegedHandler:
    """Run only fixed msiexec arguments for one fresh, safe, machine-scope product."""

    def __init__(
        self,
        resolver: SoftwareTargetResolver,
        capability: UninstallCapabilityResolver,
        validator: MsiProductValidator,
        analysis_policy: SoftwareUninstallSafetyPolicy,
        execution_policy: MachineMsiExecutionPolicy,
        preflight: SoftwareExecutionPreflight,
        uninstall: WindowsMsiUninstallPlatform,
        verifier: MsiUninstallVerifier,
        activity: MsiUninstallRepository,
        privileged_activity: PrivilegedActionRepository,
        *,
        max_items: int = 5_000,
    ) -> None:
        self._resolver = resolver
        self._capability = capability
        self._validator = validator
        self._analysis_policy = analysis_policy
        self._execution_policy = execution_policy
        self._preflight = preflight
        self._uninstall = uninstall
        self._verifier = verifier
        self._activity = activity
        self._privileged_activity = privileged_activity
        self._max_items = max_items

    @property
    def action_types(self) -> frozenset[PrivilegedActionType]:
        """Return the sole Stage 4X3 software execution action."""
        return frozenset({PrivilegedActionType.MSI_UNINSTALL_MACHINE})

    def require(self, request: PrivilegedActionRequest) -> FreshPrivilegedEvidence:
        """Repeat software, capability, MSI, policy, preflight, and activity checks."""
        payload = request.payload
        if not isinstance(payload, MachineMsiUninstallPayload):
            raise PrivilegedRevalidationError(
                BrokerDecision.ACTION_NOT_ALLOWLISTED,
                "Machine MSI handler received another payload type",
            )
        if request.risk_level is not RiskLevel.R3:
            raise PrivilegedRevalidationError(
                BrokerDecision.RISK_CHANGED,
                "Machine MSI elevated action must remain R3",
            )
        try:
            if self._activity.has_active_uninstall() or self._privileged_activity.has_active_action(
                PrivilegedActionType.MSI_UNINSTALL_MACHINE,
                exclude_plan_id=request.plan_id,
            ):
                raise PrivilegedRevalidationError(
                    BrokerDecision.PRECONDITION_FAILED,
                    "Another software uninstall transaction is active",
                )
        except MsiUninstallStoreError as exc:
            raise PrivilegedRevalidationError(
                BrokerDecision.PERSISTENCE_UNAVAILABLE,
                "Software uninstall activity could not be checked",
            ) from exc
        cancellation = CancellationToken()
        software, snapshot = self._resolver.inspect(
            payload.software_identity_digest,
            self._max_items,
            cancellation,
        )
        if (
            software is None
            or snapshot.inventory.truncated
            or snapshot.inventory.warnings
            or software.identity.canonical_digest() != request.target_identity_hash
        ):
            raise PrivilegedRevalidationError(
                BrokerDecision.TARGET_CHANGED,
                "Fresh machine software inventory is missing, partial, or changed",
            )
        raw = snapshot.raw_by_identity.get(payload.software_identity_digest)
        if raw is None:
            raise PrivilegedRevalidationError(
                BrokerDecision.TARGET_CHANGED,
                "Fresh machine software source metadata is unavailable",
            )
        capability = self._capability.resolve(software, raw)
        try:
            product = self._validator.validate_machine(software, capability)
        except (RuntimeError, ValueError) as exc:
            raise PrivilegedRevalidationError(
                BrokerDecision.MSI_REGISTRATION_CHANGED,
                "Machine MSI registration no longer validates",
            ) from exc
        if not _matches_payload(product, payload):
            raise PrivilegedRevalidationError(
                BrokerDecision.MSI_REGISTRATION_CHANGED,
                "Machine MSI identity, version, scope, or registration changed",
            )
        analysis = self._analysis_policy.assess(software)
        assessment = self._execution_policy.assess(product, analysis)
        assessment_digest = canonical_model_digest(assessment.model_dump(mode="json"))
        if (
            assessment.decision is not MsiExecutionDecision.ALLOW
            or assessment_digest != payload.execution_assessment_digest
        ):
            raise PrivilegedRevalidationError(
                BrokerDecision.SAFETY_BLOCKED,
                "Fresh machine MSI protected-class policy blocked or changed",
            )
        preflight = self._preflight.inspect(software, product, cancellation).model_copy(
            update={"privilege_expected": "required"}
        )
        if (
            preflight.state is not MsiPreflightState.READY
            or preflight.canonical_digest() != payload.preflight_digest
        ):
            raise PrivilegedRevalidationError(
                BrokerDecision.PRECONDITION_FAILED,
                "Fresh machine MSI process/service preflight blocked or changed",
            )
        state_hash = machine_msi_state_digest(
            product,
            payload.execution_assessment_digest,
            payload.preflight_digest,
        )
        return FreshPrivilegedEvidence(
            action_type=request.action_type,
            target_state_hash=state_hash,
            safety_digest=payload.execution_assessment_digest,
            validated=ValidatedMachineMsiRequest(product, preflight),
        )

    def execute_and_verify(
        self,
        request: PrivilegedActionRequest,
        fresh: FreshPrivilegedEvidence,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> PrivilegedHandlerOutcome:
        """Launch fixed msiexec, never terminate it, then refresh both inventories."""
        validated = fresh.validated
        if not isinstance(validated, ValidatedMachineMsiRequest):
            raise PrivilegedRevalidationError(
                BrokerDecision.PRECONDITION_FAILED,
                "Machine MSI validated evidence type changed",
            )
        installer = self._uninstall.uninstall(
            validated.product,
            cancellation,
            on_dispatched,
        )
        if (
            installer.launched
            and installer.category is not MsiInstallerResultCategory.MONITORING_DETACHED
        ):
            verification = self._verifier.verify(
                validated.product,
                installer,
                self._max_items,
                cancellation,
            )
            verified = verification.state is MsiVerificationState.VERIFIED_REMOVED
            post_hash = canonical_model_digest(verification.model_dump(mode="json"))
            product_present = verification.original_product_code_present
            identity_present = verification.original_identity_present
        else:
            verification = None
            verified = False
            post_hash = None
            product_present = None
            identity_present = None
        detached = installer.category is MsiInstallerResultCategory.MONITORING_DETACHED
        return PrivilegedHandlerOutcome(
            execution_started=installer.launched,
            execution_completed=installer.launched and not detached,
            verified=verified,
            uncertain=installer.launched and (detached or not verified),
            pre_state_hash=fresh.target_state_hash,
            post_state_hash=post_hash if verified else None,
            result_code=(
                "MACHINE_MSI_VERIFIED_REMOVED"
                if verified
                else (
                    "MACHINE_MSI_MONITORING_DETACHED"
                    if detached
                    else "MACHINE_MSI_COMPLETED_UNVERIFIED"
                    if installer.launched
                    else "MACHINE_MSI_NOT_STARTED"
                )
            ),
            message=(
                "Fresh Windows Installer and software inventories prove removal"
                if verified
                else "Machine MSI removal is not verified; it will not be retried"
            ),
            rollback_level=RollbackLevel.NONE,
            action_evidence=MachineMsiResultEvidence(
                installer_category=installer.category.value,
                exit_code=installer.exit_code,
                monitoring_detached=detached,
                product_registration_present_after=product_present,
                software_identity_present_after=identity_present,
            ),
        )


def machine_msi_state_digest(
    product: ValidatedMsiProduct,
    assessment_digest: str,
    preflight_digest: str,
) -> str:
    """Bind the privileged Preview to stable product, policy, and preflight evidence."""
    return canonical_model_digest(
        {
            "product_evidence": product.evidence_digest(),
            "assessment_digest": assessment_digest,
            "preflight_digest": preflight_digest,
        }
    )


def _matches_payload(
    product: ValidatedMsiProduct,
    payload: MachineMsiUninstallPayload,
) -> bool:
    return bool(
        product.product_code == payload.product_code
        and product.product_code_digest == payload.product_code_digest
        and product.identity_digest == payload.software_identity_digest
        and product.metadata_digest == payload.metadata_digest
        and product.capability_digest == payload.capability_digest
        and product.registration_digest == payload.registration_digest
        and product.display_name == payload.display_name
        and product.display_version == payload.display_version
        and product.publisher == payload.publisher
        and product.install_context is payload.install_context
        and product.scope is payload.scope
        and product.architecture is payload.architecture
        and product.source_anchor_digest == payload.source_anchor_digest
    )
