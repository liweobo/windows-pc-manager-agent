"""Translate one Stage 4C1-safe permission gap into a Stage 4X2 R3 request."""

from __future__ import annotations

from dataclasses import dataclass

from pc_manager_agent.domain.privileged_actions import (
    PrivilegedActionEnvelope,
    PrivilegedActionType,
    PrivilegeRequirement,
    PrivilegeResolution,
    PrivilegeResolutionStatus,
    ServiceStartPayload,
    ServiceStopPayload,
    canonical_model_digest,
)
from pc_manager_agent.domain.service_actions import (
    ServiceActionPlan,
    ServiceActionPreview,
    ServiceActionType,
    ServiceSafetyDecision,
    ServiceState,
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
from pc_manager_agent.orchestration.service_dependency_analyzer import (
    ServiceDependencyAnalyzer,
)
from pc_manager_agent.platform_support.service_control import ServiceControlPlatform
from pc_manager_agent.privileged.resolver import (
    PrivilegeAssessmentInput,
    PrivilegeRequirementResolver,
)
from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy


@dataclass(frozen=True, slots=True)
class PreparedElevatedServiceAction:
    """Source Stage 4C1 evidence plus the independent Stage 4X2 plan and Preview."""

    source_plan: ServiceActionPlan
    source_preview: ServiceActionPreview
    privileged: PreparedPrivilegedAction


class ElevatedServicePreparationService:
    """Prepare only exact Start/Stop actions blocked solely by standard-user SCM rights."""

    def __init__(
        self,
        privileged: PrivilegedActionService,
        coordinator: ElevatedServiceActionCoordinator,
        platform: ServiceControlPlatform,
        policy: ServiceSafetyPolicy,
        dependencies: ServiceDependencyAnalyzer,
        resolver: PrivilegeRequirementResolver | None = None,
    ) -> None:
        self._privileged = privileged
        self._coordinator = coordinator
        self._platform = platform
        self._policy = policy
        self._dependencies = dependencies
        self._resolver = resolver or PrivilegeRequirementResolver()

    def prepare(
        self,
        source_plan: ServiceActionPlan,
        source_preview: ServiceActionPreview,
    ) -> PreparedElevatedServiceAction:
        """Create a separate R3 plan only when safety passed and ordinary access did not."""
        self._require_source(source_plan, source_preview)
        payload = _payload(source_plan, source_preview)
        resolution = self._resolution(source_plan, source_preview)
        if resolution.status is not PrivilegeResolutionStatus.REQUIRED:
            raise PrivilegedActionPreparationError(
                "Fresh service evidence does not require the narrow Stage 4X2 route"
            )
        prepared = self._privileged.prepare(
            source_plan_id=source_plan.plan_id,
            source_plan_hash=source_plan.canonical_digest(),
            payload=payload,
            target_identity_hash=source_preview.observation.identity.canonical_digest(),
            object_summary=(
                f"{source_preview.observation.identity.service_name}:{source_plan.action.value}"
            ),
            target_state_hash=source_preview.observation.state_digest(),
            safety_digest=canonical_model_digest(source_preview.safety.model_dump(mode="json")),
            privilege_resolution=resolution,
        )
        return PreparedElevatedServiceAction(source_plan, source_preview, prepared)

    def approve_plan(
        self,
        prepared: PreparedElevatedServiceAction,
        approved: bool,
    ) -> object:
        """Resolve the independent Stage 4X2 plan confirmation."""
        value = prepared.privileged
        return self._privileged.resolve_plan_confirmation(
            value.plan_confirmation.confirmation_id,
            approved,
            value.plan,
            value.preview,
        )

    def prepare_runtime(
        self,
        prepared: PreparedElevatedServiceAction,
    ) -> PreparedPrivilegedRuntimeConfirmation:
        """Repeat exact service inspection before issuing the short-lived R3 gate."""
        plan = prepared.privileged.plan
        payload = plan.payload
        if not isinstance(payload, (ServiceStartPayload, ServiceStopPayload)):
            raise PrivilegedActionPreparationError("Stage 4X2 payload is not Start/Stop")
        action = (
            ServiceActionType.START
            if isinstance(payload, ServiceStartPayload)
            else ServiceActionType.STOP
        )
        observation = self._platform.inspect(payload.service_identity.service_name)
        if observation is None:
            raise PrivilegedActionPreparationError("Service disappeared before runtime Preview")
        permissions = self._platform.evaluate_permissions(
            payload.service_identity.service_name,
            action,
        )
        safety = self._policy.assess(observation, action)
        dependencies = self._dependencies.assess(observation, action)
        if (
            observation.identity.canonical_digest() != plan.target_identity_hash
            or observation.configuration_digest() != payload.expected_startup_configuration_digest
            or observation.dependency_digest() != payload.expected_dependency_digest
            or observation.state is not payload.expected_status
            or safety.decision is not ServiceSafetyDecision.ALLOW
            or not dependencies.allowed
        ):
            raise PrivilegedActionPreparationError(
                "Service identity, state, safety, or dependencies changed before confirmation"
            )
        resolution = self._resolver.resolve(
            PrivilegeAssessmentInput(
                action_type=payload.payload_type,
                safety_allowed=True,
                preflight_complete=permissions.can_query,
                current_process_elevated=permissions.process_elevated,
                current_token_has_required_access=permissions.allows(action),
                declared_requirement=PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED,
                access_failure_code=5 if permissions.can_query else None,
            )
        )
        if resolution.status is not PrivilegeResolutionStatus.REQUIRED:
            raise PrivilegedActionPreparationError(
                "Administrator routing is no longer the exact fresh permission conclusion"
            )
        return self._privileged.prepare_runtime_confirmation(
            prepared.privileged.plan_confirmation.confirmation_id,
            plan,
            target_state_hash=observation.state_digest(),
            safety_digest=canonical_model_digest(safety.model_dump(mode="json")),
            privilege_resolution=resolution,
        )

    def approve_runtime_and_build(
        self,
        prepared: PreparedElevatedServiceAction,
        runtime: PreparedPrivilegedRuntimeConfirmation,
        approved: bool,
    ) -> PrivilegedActionEnvelope:
        """Resolve the immediate gate and build one registered capability if approved."""
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
        """Delegate exactly one registered request to the UAC/IPC coordinator."""
        return self._coordinator.dispatch(envelope)

    @staticmethod
    def _require_source(
        plan: ServiceActionPlan,
        preview: ServiceActionPreview,
    ) -> None:
        if plan.action not in {ServiceActionType.START, ServiceActionType.STOP}:
            raise PrivilegedActionPreparationError("Stage 4X2 does not execute service Restart")
        if (
            preview.plan_digest != plan.canonical_digest()
            or preview.observation.identity.canonical_digest()
            != plan.target_identity.canonical_digest()
            or preview.safety.decision is not ServiceSafetyDecision.ALLOW
            or not preview.dependencies.allowed
            or preview.permissions.process_elevated
            or preview.permissions.allows(plan.action)
            or not preview.permissions.can_query
        ):
            raise PrivilegedActionPreparationError(
                "Only a complete Stage 4C1-safe ordinary-rights gap may enter Stage 4X2"
            )

    def _resolution(
        self,
        plan: ServiceActionPlan,
        preview: ServiceActionPreview,
    ) -> PrivilegeResolution:
        action_type = (
            PrivilegedActionType.SERVICE_START
            if plan.action is ServiceActionType.START
            else PrivilegedActionType.SERVICE_STOP
        )
        return self._resolver.resolve(
            PrivilegeAssessmentInput(
                action_type=action_type,
                safety_allowed=True,
                preflight_complete=preview.permissions.can_query,
                current_process_elevated=preview.permissions.process_elevated,
                current_token_has_required_access=preview.permissions.allows(plan.action),
                declared_requirement=PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED,
                access_failure_code=5,
            )
        )


def _payload(
    plan: ServiceActionPlan,
    preview: ServiceActionPreview,
) -> ServiceStartPayload | ServiceStopPayload:
    common = {
        "service_identity": preview.observation.identity,
        "expected_startup_configuration_digest": preview.observation.configuration_digest(),
        "expected_dependency_digest": preview.observation.dependency_digest(),
    }
    if plan.action is ServiceActionType.START:
        if preview.observation.state is not ServiceState.STOPPED:
            raise PrivilegedActionPreparationError("Start requires an exact STOPPED state")
        return ServiceStartPayload(**common)
    if preview.observation.state is not ServiceState.RUNNING:
        raise PrivilegedActionPreparationError("Stop requires an exact RUNNING state")
    return ServiceStopPayload(**common)
