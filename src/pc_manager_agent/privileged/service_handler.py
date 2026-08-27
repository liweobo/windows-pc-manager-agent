"""Exact Stage 4X2 service Start/Stop revalidation and SCM dispatch."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    PrivilegedActionRequest,
    PrivilegeRequirement,
    ServiceStartPayload,
    ServiceStopPayload,
    canonical_model_digest,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.service_actions import (
    ServiceActionType,
    ServiceObservation,
    ServiceSafetyDecision,
    ServiceState,
    ServiceStepResult,
)
from pc_manager_agent.orchestration.service_dependency_analyzer import (
    ServiceDependencyAnalyzer,
)
from pc_manager_agent.platform_support.service_control import ServiceControlPlatform
from pc_manager_agent.privileged.revalidation import PrivilegedRevalidationError
from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy
from pc_manager_agent.tools.manifest import CancellationToken


@dataclass(frozen=True, slots=True)
class ValidatedServiceRequest:
    """Fresh exact service observation and finite action returned by revalidation."""

    observation: ServiceObservation
    action: ServiceActionType
    safety_digest: str
    dependency_digest: str


class WindowsServicePrivilegedHandler:
    """Permit only one exact Stage 4C1-safe service Start or Stop transition."""

    def __init__(
        self,
        platform: ServiceControlPlatform,
        policy: ServiceSafetyPolicy,
        dependencies: ServiceDependencyAnalyzer,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not 5 <= timeout_seconds <= 120:
            raise ValueError("Elevated service timeout must be between 5 and 120 seconds")
        self._platform = platform
        self._policy = policy
        self._dependencies = dependencies
        self._timeout = timeout_seconds

    def require(self, request: PrivilegedActionRequest) -> ValidatedServiceRequest:
        """Repeat identity, state, policy, dependency, risk, and privilege checks."""
        payload = request.payload
        if isinstance(payload, ServiceStartPayload):
            action = ServiceActionType.START
        elif isinstance(payload, ServiceStopPayload):
            action = ServiceActionType.STOP
        else:
            raise PrivilegedRevalidationError(
                BrokerDecision.ACTION_NOT_ALLOWLISTED,
                "Stage 4X2 allow-list contains only SERVICE_START and SERVICE_STOP",
            )
        if (
            request.risk_level is not RiskLevel.R3
            or request.privilege_requirement is not PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED
        ):
            raise PrivilegedRevalidationError(
                BrokerDecision.PRIVILEGE_UNSUPPORTED,
                "Stage 4X2 requires the exact confirmed R3 Administrator route",
            )
        observation = self._platform.inspect(payload.service_identity.service_name)
        if observation is None:
            raise PrivilegedRevalidationError(
                BrokerDecision.TARGET_CHANGED,
                "Exact service no longer exists",
            )
        identity_digest = observation.identity.canonical_digest()
        if (
            identity_digest != payload.service_identity.canonical_digest()
            or identity_digest != request.target_identity_hash
        ):
            raise PrivilegedRevalidationError(
                BrokerDecision.TARGET_CHANGED,
                "Stable service identity changed",
            )
        if observation.configuration_digest() != payload.expected_startup_configuration_digest:
            raise PrivilegedRevalidationError(
                BrokerDecision.TARGET_CHANGED,
                "Service startup configuration changed",
            )
        if observation.dependency_digest() != payload.expected_dependency_digest:
            raise PrivilegedRevalidationError(
                BrokerDecision.TARGET_CHANGED,
                "Service dependency graph changed",
            )
        if observation.state is not payload.expected_status or observation.state.is_pending:
            raise PrivilegedRevalidationError(
                BrokerDecision.PRECONDITION_FAILED,
                "Service state changed after confirmation",
            )
        safety = self._policy.assess(observation, action)
        if safety.decision is not ServiceSafetyDecision.ALLOW:
            raise PrivilegedRevalidationError(
                BrokerDecision.SAFETY_BLOCKED,
                "Fresh Stage 4C1 safety policy blocked the service",
            )
        dependency = self._dependencies.assess(observation, action)
        if not dependency.allowed:
            raise PrivilegedRevalidationError(
                BrokerDecision.PRECONDITION_FAILED,
                "Dependency conditions would require a cascade operation",
            )
        return ValidatedServiceRequest(
            observation=observation,
            action=action,
            safety_digest=canonical_model_digest(safety.model_dump(mode="json")),
            dependency_digest=dependency.graph_digest,
        )

    def execute(
        self,
        request: PrivilegedActionRequest,
        validated: ValidatedServiceRequest,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> ServiceStepResult:
        """Dispatch one fixed SCM action with no shell, retry, or cascade fallback."""
        observation = validated.observation
        if validated.action is ServiceActionType.START:
            return self._platform.start(
                observation.identity,
                observation.configuration_digest(),
                ServiceState.STOPPED,
                self._timeout,
                cancellation,
                on_dispatched,
            )
        if validated.action is ServiceActionType.STOP:
            return self._platform.stop(
                observation.identity,
                observation.configuration_digest(),
                ServiceState.RUNNING,
                self._timeout,
                cancellation,
                on_dispatched,
            )
        raise PrivilegedRevalidationError(
            BrokerDecision.ACTION_NOT_ALLOWLISTED,
            "Elevated service handler received an unsupported action",
        )

    def verify(self, request: PrivilegedActionRequest) -> ServiceObservation | None:
        """Return a fresh exact observation only when the requested state is reached."""
        payload = request.payload
        if not isinstance(payload, (ServiceStartPayload, ServiceStopPayload)):
            return None
        current = self._platform.inspect(payload.service_identity.service_name)
        expected = (
            ServiceState.RUNNING
            if isinstance(payload, ServiceStartPayload)
            else ServiceState.STOPPED
        )
        if (
            current is None
            or current.identity.canonical_digest() != request.target_identity_hash
            or current.state is not expected
        ):
            return None
        return current
