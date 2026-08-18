"""Fail-closed transition and impact policy for Stage 4C2 service configuration."""

from pc_manager_agent.domain.service_actions import (
    ServiceActionType,
    ServiceObservation,
    ServiceSafetyDecision,
    ServiceStartupConfiguration,
    ServiceStartupType,
)
from pc_manager_agent.domain.service_startup_actions import (
    ServiceStartupActionType,
    ServiceStartupErrorCode,
    ServiceStartupImpact,
    ServiceStartupManagementMode,
    ServiceStartupSafetyAssessment,
)
from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy


class ServiceStartupSafetyPolicy:
    """Permit only non-delayed Automatic/Manual changes on Stage 4C1-safe services."""

    def __init__(self, base_policy: ServiceSafetyPolicy) -> None:
        self._base_policy = base_policy

    def assess(
        self,
        observation: ServiceObservation,
        action: ServiceStartupActionType,
        target: ServiceStartupConfiguration,
    ) -> ServiceStartupSafetyAssessment:
        """Classify one exact transition without trusting model or UI risk labels."""
        source = observation.startup_configuration
        base = self._base_policy.assess(observation, ServiceActionType.START)
        reasons: list[ServiceStartupErrorCode] = []
        supported = {ServiceStartupType.AUTOMATIC, ServiceStartupType.MANUAL}
        if base.decision is not ServiceSafetyDecision.ALLOW:
            reasons.append(ServiceStartupErrorCode.PROTECTED_SERVICE_BLOCKED)
        if source.startup_type in {
            ServiceStartupType.BOOT,
            ServiceStartupType.SYSTEM,
        }:
            reasons.append(ServiceStartupErrorCode.DRIVER_OR_BOOT_TYPE_BLOCKED)
        elif source.startup_type is ServiceStartupType.DISABLED or (
            target.startup_type is ServiceStartupType.DISABLED
        ):
            reasons.append(ServiceStartupErrorCode.DISABLED_TRANSITION_BLOCKED)
        elif source.startup_type is ServiceStartupType.AUTOMATIC_DELAYED or (
            target.startup_type is ServiceStartupType.AUTOMATIC_DELAYED
        ):
            reasons.append(ServiceStartupErrorCode.DELAYED_AUTO_UNSUPPORTED)
        elif source.startup_type not in supported or target.startup_type not in supported:
            reasons.append(ServiceStartupErrorCode.UNKNOWN_CONFIGURATION)
        if source.delayed_auto_start or target.delayed_auto_start:
            reasons.append(ServiceStartupErrorCode.DELAYED_AUTO_UNSUPPORTED)
        expected_target = {
            ServiceStartupActionType.SET_AUTOMATIC: ServiceStartupType.AUTOMATIC,
            ServiceStartupActionType.SET_MANUAL: ServiceStartupType.MANUAL,
            ServiceStartupActionType.RESTORE: target.startup_type,
        }[action]
        if target.startup_type is not expected_target or source == target:
            reasons.append(ServiceStartupErrorCode.UNSUPPORTED_TRANSITION)
        if observation.dependencies or observation.dependents:
            reasons.append(ServiceStartupErrorCode.DEPENDENCY_IMPACT_BLOCKED)
        unique = tuple(dict.fromkeys(reasons))
        allowed = not unique
        return ServiceStartupSafetyAssessment(
            identity_digest=observation.identity.canonical_digest(),
            source_configuration_digest=source.canonical_digest(),
            safety_class=base.safety_class,
            management_mode=(
                ServiceStartupManagementMode.RESTORE_SUPPORTED
                if allowed and action is ServiceStartupActionType.RESTORE
                else (
                    ServiceStartupManagementMode.CHANGE_SUPPORTED
                    if allowed
                    else _blocked_mode(unique)
                )
            ),
            allowed=allowed,
            reason_codes=unique,
            explanation=(
                "Exact non-delayed Automatic/Manual transition is eligible"
                if allowed
                else _explain(unique[0])
            ),
        )


def build_service_startup_impact(observation: ServiceObservation) -> ServiceStartupImpact:
    """Describe future-start effects while proving no runtime mutation is requested."""
    dependencies = tuple(
        item.service_name
        for item in sorted(observation.dependencies, key=lambda row: row.service_name.casefold())
    )
    dependents = tuple(
        item.service_name
        for item in sorted(observation.dependents, key=lambda row: row.service_name.casefold())
    )
    return ServiceStartupImpact(
        dependency_names=dependencies,
        dependent_names=dependents,
        current_runtime_state=observation.state,
        runtime_change_expected=False,
        summary=(
            "Changes future SCM startup behavior only; current service state must remain "
            f"{observation.state.value}. Dependencies: {len(dependencies)}; "
            f"dependents: {len(dependents)}."
        ),
    )


def _blocked_mode(
    reasons: tuple[ServiceStartupErrorCode, ...],
) -> ServiceStartupManagementMode:
    read_only = {
        ServiceStartupErrorCode.DELAYED_AUTO_UNSUPPORTED,
        ServiceStartupErrorCode.UNSUPPORTED_TRANSITION,
    }
    return (
        ServiceStartupManagementMode.READ_ONLY
        if reasons and all(item in read_only for item in reasons)
        else ServiceStartupManagementMode.BLOCKED
    )


def _explain(code: ServiceStartupErrorCode) -> str:
    return {
        ServiceStartupErrorCode.PROTECTED_SERVICE_BLOCKED: (
            "The service does not satisfy the Stage 4C1 third-party user-service policy"
        ),
        ServiceStartupErrorCode.DRIVER_OR_BOOT_TYPE_BLOCKED: (
            "Driver, Boot, and System startup types are always read-only"
        ),
        ServiceStartupErrorCode.DISABLED_TRANSITION_BLOCKED: (
            "Stage 4C2 never changes to or from Disabled"
        ),
        ServiceStartupErrorCode.DELAYED_AUTO_UNSUPPORTED: (
            "Delayed Automatic is displayed but cannot be changed in this version"
        ),
        ServiceStartupErrorCode.UNKNOWN_CONFIGURATION: (
            "The current startup configuration cannot be classified reliably"
        ),
        ServiceStartupErrorCode.UNSUPPORTED_TRANSITION: (
            "Only exact non-delayed Automatic and Manual transitions are supported"
        ),
        ServiceStartupErrorCode.DEPENDENCY_IMPACT_BLOCKED: (
            "Services with dependencies or dependents remain read-only in Stage 4C2"
        ),
    }[code]
