"""Main-process preparation for the three dedicated Stage 4X3 privileged capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from pc_manager_agent.domain.privileged_actions import (
    MachineMsiUninstallPayload,
    PrivilegedActionEnvelope,
    PrivilegedActionType,
    PrivilegedPayload,
    PrivilegeRequirement,
    PrivilegeResolution,
    PrivilegeResolutionStatus,
    ServiceStartupTypeChangePayload,
    ServiceStartupTypeRestorePayload,
    StartupMachineDisablePayload,
    StartupMachineRestorePayload,
    canonical_model_digest,
)
from pc_manager_agent.domain.service_actions import (
    ServiceObservation,
    ServiceStableIdentity,
    ServiceStartupConfiguration,
    ServiceStartupType,
)
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionType,
    ServiceStartupBackupPayload,
)
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiExecutionDecision,
    MsiPreflightState,
    ValidatedMsiProduct,
)
from pc_manager_agent.domain.startup_actions import (
    StartupActionType,
    StartupIdentity,
    StartupObservation,
    StartupSafetyDecision,
)
from pc_manager_agent.orchestration.elevated_service_actions import (
    ElevatedDispatchOutcome,
    ElevatedServiceActionCoordinator,
)
from pc_manager_agent.orchestration.privileged_actions import (
    PreparedPrivilegedAction,
    PreparedPrivilegedRuntimeConfirmation,
    PrivilegedActionPreparationError,
    PrivilegedActionService,
)
from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
from pc_manager_agent.orchestration.software_execution_preflight import SoftwareExecutionPreflight
from pc_manager_agent.orchestration.software_msi_validation import MsiProductValidator
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.persistence.privileged_actions import PrivilegedActionRepository
from pc_manager_agent.persistence.service_startup_actions import (
    ServiceStartupActionRepository,
    ServiceStartupBackupVault,
)
from pc_manager_agent.persistence.software_uninstall_execution import MsiUninstallRepository
from pc_manager_agent.persistence.startup_actions import StartupActionRepository, StartupBackupVault
from pc_manager_agent.platform_support.service_control import ServiceControlPlatform
from pc_manager_agent.platform_support.service_startup import ServiceStartupPlatform
from pc_manager_agent.platform_support.startup import StartupManagementPlatform
from pc_manager_agent.platform_support.windows.startup_management import machine_absent_state_digest
from pc_manager_agent.privileged.machine_msi_handler import machine_msi_state_digest
from pc_manager_agent.privileged.resolver import (
    PrivilegeAssessmentInput,
    PrivilegeRequirementResolver,
)
from pc_manager_agent.safety.machine_msi_policy import MachineMsiExecutionPolicy
from pc_manager_agent.safety.machine_startup_policy import MachineStartupSafetyPolicy
from pc_manager_agent.safety.service_startup_policy import (
    ServiceStartupSafetyPolicy,
    build_service_startup_impact,
)
from pc_manager_agent.safety.software_uninstall_policy import SoftwareUninstallSafetyPolicy
from pc_manager_agent.tools.manifest import CancellationToken


@dataclass(frozen=True, slots=True)
class PreparedStage4X3Action:
    """Business evidence plus one separate R3 plan and Preview."""

    user_goal: str
    source_transaction_id: UUID
    privileged: PreparedPrivilegedAction


class ElevatedStage4X3PreparationService:
    """Create typed Broker requests only after existing deterministic policies pass."""

    def __init__(
        self,
        privileged: PrivilegedActionService,
        coordinator: ElevatedServiceActionCoordinator,
        control: ServiceControlPlatform,
        service_startup: ServiceStartupPlatform,
        service_policy: ServiceStartupSafetyPolicy,
        service_vault: ServiceStartupBackupVault,
        service_history: ServiceStartupActionRepository,
        startup: StartupManagementPlatform,
        startup_policy: MachineStartupSafetyPolicy,
        startup_vault: StartupBackupVault,
        startup_history: StartupActionRepository,
        software: SoftwareTargetResolver,
        capability: UninstallCapabilityResolver,
        msi_validator: MsiProductValidator,
        software_policy: SoftwareUninstallSafetyPolicy,
        msi_policy: MachineMsiExecutionPolicy,
        msi_preflight: SoftwareExecutionPreflight,
        uninstall_activity: MsiUninstallRepository,
        privileged_activity: PrivilegedActionRepository,
        resolver: PrivilegeRequirementResolver | None = None,
        *,
        max_items: int = 5_000,
    ) -> None:
        self._privileged = privileged
        self._coordinator = coordinator
        self._control = control
        self._service_startup = service_startup
        self._service_policy = service_policy
        self._service_vault = service_vault
        self._service_history = service_history
        self._startup = startup
        self._startup_policy = startup_policy
        self._startup_vault = startup_vault
        self._startup_history = startup_history
        self._software = software
        self._capability = capability
        self._msi_validator = msi_validator
        self._software_policy = software_policy
        self._msi_policy = msi_policy
        self._msi_preflight = msi_preflight
        self._uninstall_activity = uninstall_activity
        self._privileged_activity = privileged_activity
        self._resolver = resolver or PrivilegeRequirementResolver()
        self._max_items = max_items

    def prepare_service_startup_change(
        self,
        user_goal: str,
        identity: ServiceStableIdentity,
        action: ServiceStartupActionType,
    ) -> PreparedStage4X3Action:
        """Prepare only non-delayed Automatic/Manual changes lacking change-config access."""
        if action not in {
            ServiceStartupActionType.SET_AUTOMATIC,
            ServiceStartupActionType.SET_MANUAL,
        }:
            raise PrivilegedActionPreparationError("Use the independent restore preparation")
        observation = self._require_service(identity)
        target = _service_target(action)
        safety = self._service_policy.assess(observation, action, target)
        permissions = self._service_startup.evaluate_permissions(identity.service_name)
        resolution = self._require_admin_resolution(
            PrivilegedActionType.SERVICE_STARTUP_TYPE_CHANGE,
            safety_allowed=safety.allowed,
            preflight_complete=permissions.can_query_configuration,
            process_elevated=permissions.process_elevated,
            standard_access=permissions.allows_change,
        )
        if not safety.allowed:
            raise PrivilegedActionPreparationError(safety.explanation)
        impact = build_service_startup_impact(observation)
        source_id = uuid4()
        backup = self._service_vault.store(
            ServiceStartupBackupPayload(
                stable_identity=observation.identity,
                display_name=observation.display_name,
                original_configuration=observation.startup_configuration,
                original_runtime_state=observation.state,
            )
        )
        safety_digest = canonical_model_digest(safety.model_dump(mode="json"))
        payload = ServiceStartupTypeChangePayload(
            source_transaction_id=source_id,
            service_identity=observation.identity,
            expected_current_configuration=observation.startup_configuration,
            requested_startup_type=target.startup_type,
            expected_runtime_state=observation.state,
            impact_digest=impact.canonical_digest(),
            safety_digest=safety_digest,
            backup_id=backup.backup_id,
            backup_digest=backup.payload_digest,
        )
        return self._prepare(
            user_goal,
            source_id,
            payload,
            observation.identity.canonical_digest(),
            f"{identity.service_name}:startup:{target.startup_type.value}",
            observation.state_digest(),
            safety_digest,
            resolution,
        )

    def prepare_service_startup_restore(
        self,
        user_goal: str,
        backup_id: UUID,
    ) -> PreparedStage4X3Action:
        """Prepare one conflict-checked restore from an Agent-owned verified change."""
        record = self._service_history.get_change(backup_id)
        backup = self._service_vault.load(backup_id, expected_digest=record.backup_digest)
        observation = self._require_service(record.stable_identity)
        if observation.startup_configuration != record.written_configuration:
            raise PrivilegedActionPreparationError(
                "Current service configuration conflicts with restore"
            )
        action = ServiceStartupActionType.RESTORE
        safety = self._service_policy.assess(
            observation,
            action,
            backup.original_configuration,
        )
        permissions = self._service_startup.evaluate_permissions(
            record.stable_identity.service_name
        )
        resolution = self._require_admin_resolution(
            PrivilegedActionType.SERVICE_STARTUP_TYPE_RESTORE,
            safety_allowed=safety.allowed,
            preflight_complete=permissions.can_query_configuration,
            process_elevated=permissions.process_elevated,
            standard_access=permissions.allows_change,
        )
        if not safety.allowed:
            raise PrivilegedActionPreparationError(safety.explanation)
        impact = build_service_startup_impact(observation)
        source_id = uuid4()
        safety_digest = canonical_model_digest(safety.model_dump(mode="json"))
        payload = ServiceStartupTypeRestorePayload(
            source_transaction_id=source_id,
            original_change_transaction_id=record.original_transaction_id,
            service_identity=record.stable_identity,
            expected_current_configuration=record.written_configuration,
            target_original_configuration=record.original_configuration,
            expected_runtime_state=observation.state,
            impact_digest=impact.canonical_digest(),
            safety_digest=safety_digest,
            backup_id=backup_id,
            backup_digest=record.backup_digest,
        )
        return self._prepare(
            user_goal,
            source_id,
            payload,
            record.stable_identity.canonical_digest(),
            f"{record.stable_identity.service_name}:startup:restore",
            observation.state_digest(),
            safety_digest,
            resolution,
        )

    def prepare_machine_startup_disable(
        self,
        user_goal: str,
        identity: StartupIdentity,
    ) -> PreparedStage4X3Action:
        """Prepare one exact explicit-view HKLM Run disable with encrypted backup."""
        observation = self._require_machine_startup(identity)
        safety = self._startup_policy.assess(observation, StartupActionType.DISABLE)
        if safety.decision is not StartupSafetyDecision.ALLOW:
            raise PrivilegedActionPreparationError(safety.explanation)
        source_id = uuid4()
        backup_id = uuid4()
        backup_payload = self._startup.capture_backup(identity, backup_id)
        backup = self._startup_vault.store(backup_payload, backup_id=backup_id)
        safety_digest = canonical_model_digest(safety.model_dump(mode="json"))
        registry = identity.registry
        if registry is None:
            raise PrivilegedActionPreparationError("Machine startup registry identity is missing")
        payload = StartupMachineDisablePayload(
            source_transaction_id=source_id,
            registry_identity=registry,
            startup_identity_digest=identity.canonical_digest(),
            expected_state_digest=observation.current_state_digest(),
            safety_digest=safety_digest,
            backup_id=backup.backup_id,
            backup_digest=backup.payload_digest,
        )
        resolution = self._require_admin_resolution(
            PrivilegedActionType.STARTUP_MACHINE_DISABLE,
            safety_allowed=True,
            preflight_complete=True,
            process_elevated=False,
            standard_access=False,
        )
        return self._prepare(
            user_goal,
            source_id,
            payload,
            identity.canonical_digest(),
            f"HKLM Run:{registry.registry_view}:{registry.value_name}:disable",
            observation.current_state_digest(),
            safety_digest,
            resolution,
        )

    def prepare_machine_startup_restore(
        self,
        user_goal: str,
        backup_id: UUID,
    ) -> PreparedStage4X3Action:
        """Prepare an exact restore only while the original HKLM value remains absent."""
        record = self._startup_history.get_disabled(backup_id)
        backup = self._startup_vault.load(backup_id, expected_digest=record.backup_digest)
        if self._startup.inspect(record.identity) is not None:
            raise PrivilegedActionPreparationError(
                "A newer startup value occupies the restore target"
            )
        safety = self._startup_policy.assess(record.original_observation, StartupActionType.RESTORE)
        if safety.decision is not StartupSafetyDecision.ALLOW:
            raise PrivilegedActionPreparationError(safety.explanation)
        registry = record.identity.registry
        if registry is None or backup.original_identity != record.identity:
            raise PrivilegedActionPreparationError("Machine startup restore backup binding changed")
        source_id = uuid4()
        state_digest = machine_absent_state_digest(record.identity)
        safety_digest = canonical_model_digest(safety.model_dump(mode="json"))
        payload = StartupMachineRestorePayload(
            source_transaction_id=source_id,
            original_disable_transaction_id=record.original_transaction_id,
            registry_identity=registry,
            startup_identity_digest=record.identity.canonical_digest(),
            expected_state_digest=state_digest,
            safety_digest=safety_digest,
            backup_id=backup_id,
            backup_digest=record.backup_digest,
        )
        resolution = self._require_admin_resolution(
            PrivilegedActionType.STARTUP_MACHINE_RESTORE,
            safety_allowed=True,
            preflight_complete=True,
            process_elevated=False,
            standard_access=False,
        )
        return self._prepare(
            user_goal,
            source_id,
            payload,
            record.identity.canonical_digest(),
            f"HKLM Run:{registry.registry_view}:{registry.value_name}:restore",
            state_digest,
            safety_digest,
            resolution,
        )

    def prepare_machine_msi_uninstall(
        self,
        user_goal: str,
        software_identity_digest: str,
    ) -> PreparedStage4X3Action:
        """Prepare one exact high-confidence machine MSI after protected-class preflight."""
        if (
            self._uninstall_activity.has_active_uninstall()
            or self._privileged_activity.has_active_action(
                PrivilegedActionType.MSI_UNINSTALL_MACHINE
            )
        ):
            raise PrivilegedActionPreparationError("Another uninstall transaction is active")
        product, assessment_digest, preflight_digest = self._fresh_machine_msi(
            software_identity_digest
        )
        source_id = uuid4()
        payload = _machine_msi_payload(
            source_id,
            product,
            assessment_digest,
            preflight_digest,
        )
        resolution = self._require_admin_resolution(
            PrivilegedActionType.MSI_UNINSTALL_MACHINE,
            safety_allowed=True,
            preflight_complete=True,
            process_elevated=False,
            standard_access=False,
        )
        target_state = machine_msi_state_digest(product, assessment_digest, preflight_digest)
        return self._prepare(
            user_goal,
            source_id,
            payload,
            product.identity_digest,
            f"MSI:{product.product_code_digest}:machine-uninstall",
            target_state,
            assessment_digest,
            resolution,
        )

    def approve_plan(self, prepared: PreparedStage4X3Action, approved: bool) -> object:
        """Resolve the durable business/R3 plan confirmation."""
        value = prepared.privileged
        return self._privileged.resolve_plan_confirmation(
            value.plan_confirmation.confirmation_id,
            approved,
            value.plan,
            value.preview,
        )

    def prepare_runtime(
        self,
        prepared: PreparedStage4X3Action,
    ) -> PreparedPrivilegedRuntimeConfirmation:
        """Repeat Main-side identity, safety, backup, and preflight before immediate approval."""
        plan = prepared.privileged.plan
        target_state, safety_digest, resolution = self._refresh(
            plan.payload,
            current_plan_id=plan.plan_id,
        )
        return self._privileged.prepare_runtime_confirmation(
            prepared.privileged.plan_confirmation.confirmation_id,
            plan,
            target_state_hash=target_state,
            safety_digest=safety_digest,
            privilege_resolution=resolution,
        )

    def approve_runtime_and_build(
        self,
        prepared: PreparedStage4X3Action,
        runtime: PreparedPrivilegedRuntimeConfirmation,
        approved: bool,
    ) -> PrivilegedActionEnvelope:
        """Consume the immediate confirmation and build one single-use Broker request."""
        confirmation = self._privileged.resolve_runtime_confirmation(
            runtime.runtime_confirmation.confirmation_id,
            approved,
            prepared.privileged.plan,
            runtime.preview,
        )
        if not approved:
            raise PrivilegedActionPreparationError("Runtime confirmation was rejected")
        return self._privileged.build_and_register(
            prepared.privileged.plan,
            runtime.preview,
            plan_confirmation_id=prepared.privileged.plan_confirmation.confirmation_id,
            runtime_confirmation_id=confirmation.confirmation_id,
        )

    def dispatch(self, envelope: PrivilegedActionEnvelope) -> ElevatedDispatchOutcome:
        """Launch one short-lived Broker attempt; never retry or fall back to a shell."""
        return self._coordinator.dispatch(envelope)

    def _prepare(
        self,
        user_goal: str,
        source_id: UUID,
        payload: PrivilegedPayload,
        target_identity_hash: str,
        object_summary: str,
        target_state_hash: str,
        safety_digest: str,
        resolution: PrivilegeResolution,
    ) -> PreparedStage4X3Action:
        prepared = self._privileged.prepare(
            source_plan_id=source_id,
            source_plan_hash=canonical_model_digest(
                {"source_transaction_id": str(source_id), "user_goal": user_goal}
            ),
            payload=payload,
            target_identity_hash=target_identity_hash,
            object_summary=object_summary,
            target_state_hash=target_state_hash,
            safety_digest=safety_digest,
            privilege_resolution=resolution,
        )
        return PreparedStage4X3Action(user_goal, source_id, prepared)

    def _refresh(
        self,
        payload: object,
        *,
        current_plan_id: UUID,
    ) -> tuple[str, str, PrivilegeResolution]:
        if isinstance(payload, (ServiceStartupTypeChangePayload, ServiceStartupTypeRestorePayload)):
            return self._refresh_service(payload)
        if isinstance(payload, (StartupMachineDisablePayload, StartupMachineRestorePayload)):
            return self._refresh_startup(payload)
        if isinstance(payload, MachineMsiUninstallPayload):
            return self._refresh_msi(payload, current_plan_id=current_plan_id)
        raise PrivilegedActionPreparationError("Stage 4X3 payload has no Main revalidator")

    def _refresh_service(
        self,
        payload: ServiceStartupTypeChangePayload | ServiceStartupTypeRestorePayload,
    ) -> tuple[str, str, PrivilegeResolution]:
        observation = self._require_service(payload.service_identity)
        action = (
            _service_action(payload.requested_startup_type)
            if isinstance(payload, ServiceStartupTypeChangePayload)
            else ServiceStartupActionType.RESTORE
        )
        target = (
            _service_target(action)
            if isinstance(payload, ServiceStartupTypeChangePayload)
            else payload.target_original_configuration
        )
        if (
            observation.startup_configuration != payload.expected_current_configuration
            or observation.state is not payload.expected_runtime_state
            or build_service_startup_impact(observation).canonical_digest() != payload.impact_digest
        ):
            raise PrivilegedActionPreparationError("Service configuration or impact changed")
        self._service_vault.load(payload.backup_id, expected_digest=payload.backup_digest)
        if isinstance(payload, ServiceStartupTypeRestorePayload):
            record = self._service_history.get_change(payload.backup_id)
            if record.original_transaction_id != payload.original_change_transaction_id:
                raise PrivilegedActionPreparationError("Service restore history changed")
        safety = self._service_policy.assess(observation, action, target)
        safety_digest = canonical_model_digest(safety.model_dump(mode="json"))
        if not safety.allowed or safety_digest != payload.safety_digest:
            raise PrivilegedActionPreparationError("Service startup safety changed")
        permissions = self._service_startup.evaluate_permissions(
            payload.service_identity.service_name
        )
        resolution = self._require_admin_resolution(
            payload.payload_type,
            safety_allowed=True,
            preflight_complete=permissions.can_query_configuration,
            process_elevated=permissions.process_elevated,
            standard_access=permissions.allows_change,
        )
        return observation.state_digest(), safety_digest, resolution

    def _refresh_startup(
        self,
        payload: StartupMachineDisablePayload | StartupMachineRestorePayload,
    ) -> tuple[str, str, PrivilegeResolution]:
        identity = StartupIdentity(source=payload.source, registry=payload.registry_identity)
        self._startup_vault.load(payload.backup_id, expected_digest=payload.backup_digest)
        if isinstance(payload, StartupMachineDisablePayload):
            observation = self._require_machine_startup(identity)
            state = observation.current_state_digest()
            original = observation
            action = StartupActionType.DISABLE
        else:
            record = self._startup_history.get_disabled(payload.backup_id)
            if (
                record.original_transaction_id != payload.original_disable_transaction_id
                or self._startup.inspect(identity) is not None
            ):
                raise PrivilegedActionPreparationError("Machine startup restore conflicts")
            state = machine_absent_state_digest(identity)
            original = record.original_observation
            action = StartupActionType.RESTORE
        safety = self._startup_policy.assess(original, action)
        safety_digest = canonical_model_digest(safety.model_dump(mode="json"))
        if (
            safety.decision is not StartupSafetyDecision.ALLOW
            or state != payload.expected_state_digest
            or safety_digest != payload.safety_digest
        ):
            raise PrivilegedActionPreparationError("Machine startup state or safety changed")
        resolution = self._require_admin_resolution(
            payload.payload_type,
            safety_allowed=True,
            preflight_complete=True,
            process_elevated=False,
            standard_access=False,
        )
        return state, safety_digest, resolution

    def _refresh_msi(
        self,
        payload: MachineMsiUninstallPayload,
        *,
        current_plan_id: UUID,
    ) -> tuple[str, str, PrivilegeResolution]:
        if (
            self._uninstall_activity.has_active_uninstall()
            or self._privileged_activity.has_active_action(
                PrivilegedActionType.MSI_UNINSTALL_MACHINE,
                exclude_plan_id=current_plan_id,
            )
        ):
            # The current plan is not SIGNED yet at runtime confirmation. Any active plan blocks.
            raise PrivilegedActionPreparationError("Another uninstall transaction is active")
        product, assessment_digest, preflight_digest = self._fresh_machine_msi(
            payload.software_identity_digest
        )
        if (
            _machine_msi_payload(
                payload.source_transaction_id,
                product,
                assessment_digest,
                preflight_digest,
            )
            != payload
        ):
            raise PrivilegedActionPreparationError(
                "Machine MSI identity, policy, or preflight changed"
            )
        resolution = self._require_admin_resolution(
            payload.payload_type,
            safety_allowed=True,
            preflight_complete=True,
            process_elevated=False,
            standard_access=False,
        )
        return (
            machine_msi_state_digest(product, assessment_digest, preflight_digest),
            assessment_digest,
            resolution,
        )

    def _fresh_machine_msi(
        self,
        identity_digest: str,
    ) -> tuple[ValidatedMsiProduct, str, str]:
        cancellation = CancellationToken()
        software, snapshot = self._software.inspect(identity_digest, self._max_items, cancellation)
        if software is None or snapshot.inventory.truncated or snapshot.inventory.warnings:
            raise PrivilegedActionPreparationError("Fresh software inventory is missing or partial")
        raw = snapshot.raw_by_identity.get(identity_digest)
        if raw is None:
            raise PrivilegedActionPreparationError("Fresh MSI source metadata is unavailable")
        capability = self._capability.resolve(software, raw)
        product = self._msi_validator.validate_machine(software, capability)
        assessment = self._msi_policy.assess(product, self._software_policy.assess(software))
        if assessment.decision is not MsiExecutionDecision.ALLOW:
            raise PrivilegedActionPreparationError(
                "; ".join(assessment.reasons) or "Machine MSI safety policy blocked"
            )
        preflight = self._msi_preflight.inspect(software, product, cancellation).model_copy(
            update={"privilege_expected": "required"}
        )
        if preflight.state is not MsiPreflightState.READY:
            raise PrivilegedActionPreparationError("Machine MSI process/service preflight blocked")
        return (
            product,
            canonical_model_digest(assessment.model_dump(mode="json")),
            preflight.canonical_digest(),
        )

    def _require_service(self, identity: ServiceStableIdentity) -> ServiceObservation:
        observation = self._control.inspect(identity.service_name)
        if (
            observation is None
            or observation.identity.canonical_digest() != identity.canonical_digest()
        ):
            raise PrivilegedActionPreparationError("Exact service identity is missing or changed")
        return observation

    def _require_machine_startup(self, identity: StartupIdentity) -> StartupObservation:
        observation = self._startup.inspect(identity)
        if (
            observation is None
            or observation.identity.canonical_digest() != identity.canonical_digest()
        ):
            raise PrivilegedActionPreparationError(
                "Exact machine startup identity is missing or changed"
            )
        return observation

    def _require_admin_resolution(
        self,
        action_type: PrivilegedActionType,
        *,
        safety_allowed: bool,
        preflight_complete: bool,
        process_elevated: bool,
        standard_access: bool,
    ) -> PrivilegeResolution:
        resolution = self._resolver.resolve(
            PrivilegeAssessmentInput(
                action_type=action_type,
                safety_allowed=safety_allowed,
                preflight_complete=preflight_complete,
                current_process_elevated=process_elevated,
                current_token_has_required_access=standard_access,
                declared_requirement=PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED,
                access_failure_code=5 if not standard_access else None,
            )
        )
        if resolution.status is not PrivilegeResolutionStatus.REQUIRED:
            raise PrivilegedActionPreparationError(
                "Fresh deterministic evidence does not require the elevated Broker route"
            )
        return resolution


def _service_target(action: ServiceStartupActionType) -> ServiceStartupConfiguration:
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
    raise PrivilegedActionPreparationError("Restore target must come from a verified backup")


def _service_action(startup_type: ServiceStartupType) -> ServiceStartupActionType:
    return (
        ServiceStartupActionType.SET_AUTOMATIC
        if startup_type is ServiceStartupType.AUTOMATIC
        else ServiceStartupActionType.SET_MANUAL
    )


def _machine_msi_payload(
    source_id: UUID,
    product: ValidatedMsiProduct,
    assessment_digest: str,
    preflight_digest: str,
) -> MachineMsiUninstallPayload:
    return MachineMsiUninstallPayload(
        source_transaction_id=source_id,
        product_code=product.product_code,
        product_code_digest=product.product_code_digest,
        software_identity_digest=product.identity_digest,
        metadata_digest=product.metadata_digest,
        capability_digest=product.capability_digest,
        registration_digest=product.registration_digest,
        execution_assessment_digest=assessment_digest,
        preflight_digest=preflight_digest,
        display_name=product.display_name,
        display_version=product.display_version,
        publisher=product.publisher,
        install_context=product.install_context,
        scope=product.scope,
        architecture=product.architecture,
        source_anchor_digest=product.source_anchor_digest,
    )
