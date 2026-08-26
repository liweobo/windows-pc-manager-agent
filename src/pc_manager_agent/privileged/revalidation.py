"""Action-specific fresh state, safety, risk, and TOCTOU revalidation."""

from __future__ import annotations

import threading

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    PrivilegedActionRequest,
    PrivilegeRequirement,
    PrivilegeResolution,
    PrivilegeResolutionStatus,
    ServiceStartPayload,
    ServiceStopPayload,
    canonical_model_digest,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.service_actions import (
    ServiceStableIdentity,
    ServiceStartupConfiguration,
    ServiceState,
)


class PrivilegedRevalidationError(RuntimeError):
    """Fresh deterministic Broker rejection with a stable decision code."""

    def __init__(self, decision: BrokerDecision, message: str) -> None:
        super().__init__(message)
        self.decision = decision


class FakePrivilegedService(FrozenModel):
    """Synthetic service evidence used only by the Stage 4X1 Mock Broker."""

    identity: ServiceStableIdentity
    state: ServiceState
    startup_configuration: ServiceStartupConfiguration
    dependency_digest: str
    safety_allowed: bool
    safety_digest: str
    risk_level: RiskLevel = RiskLevel.R3
    privilege_resolution: PrivilegeResolution

    def state_digest(self) -> str:
        """Hash target state separately from safety, risk, and privilege evidence."""
        return canonical_model_digest(
            {
                "identity": self.identity.model_dump(mode="json"),
                "state": self.state.value,
                "startup_configuration": self.startup_configuration.model_dump(mode="json"),
                "dependency_digest": self.dependency_digest,
            }
        )


class FakePrivilegedSystemState:
    """Thread-safe fake state; it never queries or modifies Windows SCM."""

    def __init__(self, services: tuple[FakePrivilegedService, ...] = ()) -> None:
        self._lock = threading.RLock()
        self._services = {item.identity.service_name.casefold(): item for item in services}
        self._fail_actions: set[str] = set()

    def inspect_service(self, service_name: str) -> FakePrivilegedService | None:
        """Return a snapshot of one synthetic service."""
        with self._lock:
            return self._services.get(service_name.casefold())

    def replace_service(self, service: FakePrivilegedService) -> None:
        """Replace synthetic evidence to exercise TOCTOU behavior in tests."""
        with self._lock:
            self._services[service.identity.service_name.casefold()] = service

    def set_action_failure(self, service_name: str, enabled: bool = True) -> None:
        """Configure one synthetic target to fail after Broker approval."""
        with self._lock:
            key = service_name.casefold()
            if enabled:
                self._fail_actions.add(key)
            else:
                self._fail_actions.discard(key)

    def set_service_state(self, service_name: str, state: ServiceState) -> bool:
        """Mutate only fake state and report whether the synthetic operation succeeded."""
        with self._lock:
            key = service_name.casefold()
            current = self._services.get(key)
            if current is None or key in self._fail_actions:
                return False
            self._services[key] = current.model_copy(update={"state": state})
            return True


class ServicePrivilegedRevalidator:
    """Revalidate Start/Stop requests against fresh synthetic service evidence."""

    def __init__(self, state: FakePrivilegedSystemState) -> None:
        self._state = state

    def require(self, request: PrivilegedActionRequest) -> FakePrivilegedService:
        """Return fresh evidence or raise the exact fail-closed Broker decision."""
        payload = request.payload
        if not isinstance(payload, (ServiceStartPayload, ServiceStopPayload)):
            raise PrivilegedRevalidationError(
                BrokerDecision.ACTION_NOT_ALLOWLISTED,
                "Stage 4X1 Mock revalidator supports only service start and stop",
            )
        current = self._state.inspect_service(payload.service_identity.service_name)
        if current is None:
            raise PrivilegedRevalidationError(
                BrokerDecision.TARGET_CHANGED, "Synthetic service no longer exists"
            )
        if (
            current.identity.canonical_digest() != request.target_identity_hash
            or current.identity.canonical_digest() != payload.service_identity.canonical_digest()
        ):
            raise PrivilegedRevalidationError(
                BrokerDecision.TARGET_CHANGED, "Stable service identity changed"
            )
        if not current.safety_allowed:
            raise PrivilegedRevalidationError(
                BrokerDecision.SAFETY_BLOCKED, "Fresh service safety policy blocked the target"
            )
        if current.risk_level is not request.risk_level:
            raise PrivilegedRevalidationError(
                BrokerDecision.RISK_CHANGED, "Fresh service risk differs from approval"
            )
        resolution = current.privilege_resolution
        if (
            resolution.status is not PrivilegeResolutionStatus.REQUIRED
            or resolution.requirement is not PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED
            or request.privilege_requirement is not PrivilegeRequirement.ELEVATED_ADMIN_REQUIRED
        ):
            raise PrivilegedRevalidationError(
                BrokerDecision.PRIVILEGE_UNSUPPORTED,
                "Fresh privilege requirement is no longer exact Administrator",
            )
        if current.state is not payload.expected_status:
            raise PrivilegedRevalidationError(
                BrokerDecision.PRECONDITION_FAILED, "Synthetic service state changed"
            )
        if (
            current.startup_configuration.canonical_digest()
            != payload.expected_startup_configuration_digest
            or current.dependency_digest != payload.expected_dependency_digest
        ):
            raise PrivilegedRevalidationError(
                BrokerDecision.TARGET_CHANGED,
                "Synthetic startup configuration or dependency evidence changed",
            )
        return current

    def execute(self, request: PrivilegedActionRequest) -> bool:
        """Apply only the finite start/stop transition to fake state."""
        payload = request.payload
        if isinstance(payload, ServiceStartPayload):
            return self._state.set_service_state(
                payload.service_identity.service_name, ServiceState.RUNNING
            )
        if isinstance(payload, ServiceStopPayload):
            return self._state.set_service_state(
                payload.service_identity.service_name, ServiceState.STOPPED
            )
        return False

    def verify(self, request: PrivilegedActionRequest) -> FakePrivilegedService | None:
        """Read fresh fake state and return it only when the postcondition holds."""
        payload = request.payload
        if not isinstance(payload, (ServiceStartPayload, ServiceStopPayload)):
            return None
        current = self._state.inspect_service(payload.service_identity.service_name)
        expected = (
            ServiceState.RUNNING
            if isinstance(payload, ServiceStartPayload)
            else ServiceState.STOPPED
        )
        return current if current is not None and current.state is expected else None
