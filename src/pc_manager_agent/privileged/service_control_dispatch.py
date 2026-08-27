"""Adapter from the proven Stage 4X2 service handler to the Stage 4X3 dispatcher."""

from __future__ import annotations

from collections.abc import Callable

from pc_manager_agent.domain.elevated_broker import ServiceControlResultEvidence
from pc_manager_agent.domain.privileged_actions import (
    BrokerDecision,
    PrivilegedActionRequest,
    PrivilegedActionType,
)
from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.privileged.dispatcher import (
    FreshPrivilegedEvidence,
    PrivilegedHandlerOutcome,
)
from pc_manager_agent.privileged.revalidation import PrivilegedRevalidationError
from pc_manager_agent.privileged.service_handler import (
    ValidatedServiceRequest,
    WindowsServicePrivilegedHandler,
)
from pc_manager_agent.tools.manifest import CancellationToken


class ServiceControlDispatchHandler:
    """Keep service Start/Stop behavior unchanged behind the typed dispatcher."""

    def __init__(self, handler: WindowsServicePrivilegedHandler) -> None:
        self._handler = handler

    @property
    def action_types(self) -> frozenset[PrivilegedActionType]:
        """Return the two exact Stage 4X2 lifecycle actions."""
        return frozenset({PrivilegedActionType.SERVICE_START, PrivilegedActionType.SERVICE_STOP})

    def require(self, request: PrivilegedActionRequest) -> FreshPrivilegedEvidence:
        """Delegate all established Stage 4C1 identity, policy, and dependency checks."""
        validated = self._handler.require(request)
        return FreshPrivilegedEvidence(
            action_type=request.action_type,
            target_state_hash=validated.observation.state_digest(),
            safety_digest=validated.safety_digest,
            validated=validated,
        )

    def execute_and_verify(
        self,
        request: PrivilegedActionRequest,
        fresh: FreshPrivilegedEvidence,
        cancellation: CancellationToken,
        on_dispatched: Callable[[], None] | None = None,
    ) -> PrivilegedHandlerOutcome:
        """Dispatch one SCM lifecycle call and perform a new exact readback."""
        validated = fresh.validated
        if not isinstance(validated, ValidatedServiceRequest):
            raise PrivilegedRevalidationError(
                BrokerDecision.PRECONDITION_FAILED,
                "Service control validated evidence type changed",
            )
        step = self._handler.execute(request, validated, cancellation, on_dispatched)
        verified = self._handler.verify(request)
        success = step.control_dispatched and step.verified and verified is not None
        after_state = verified.state if verified is not None else step.after_state
        return PrivilegedHandlerOutcome(
            execution_started=step.control_dispatched,
            execution_completed=step.control_dispatched,
            verified=success,
            uncertain=False,
            pre_state_hash=validated.observation.state_digest(),
            post_state_hash=verified.state_digest() if verified is not None else None,
            result_code="REAL_SERVICE_ACTION_VERIFIED" if success else "VERIFICATION_FAILED",
            message=(
                "The exact SCM action and Broker postcondition were verified"
                if success
                else "The exact service postcondition could not be verified"
            ),
            rollback_level=RollbackLevel.MANUAL,
            action_evidence=(
                ServiceControlResultEvidence(
                    before_state=step.before_state,
                    after_state=after_state,
                )
                if step.control_dispatched
                else None
            ),
        )
