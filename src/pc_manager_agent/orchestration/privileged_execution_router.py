"""Deterministic standard-user versus one-shot Broker execution routing."""

from __future__ import annotations

from enum import StrEnum

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.privileged_actions import (
    PrivilegeResolution,
    PrivilegeResolutionStatus,
)


class PrivilegedExecutionRoute(StrEnum):
    """Finite destinations; there is no generic elevated-command destination."""

    STANDARD_EXECUTOR = "STANDARD_EXECUTOR"
    ELEVATED_BROKER = "ELEVATED_BROKER"
    BLOCKED = "BLOCKED"


class PrivilegedExecutionRoutingDecision(FrozenModel):
    """Auditable route derived only from a deterministic privilege resolution."""

    route: PrivilegedExecutionRoute
    reason_code: str
    resolution_digest: str


class PrivilegedExecutionRouter:
    """Select the ordinary backend or Broker without consulting an LLM."""

    def route(self, resolution: PrivilegeResolution) -> PrivilegedExecutionRoutingDecision:
        """Map REQUIRED to Broker, NOT_REQUIRED to ordinary execution, and deny everything else."""
        if resolution.status is PrivilegeResolutionStatus.NOT_REQUIRED:
            route = PrivilegedExecutionRoute.STANDARD_EXECUTOR
        elif resolution.status is PrivilegeResolutionStatus.REQUIRED:
            route = PrivilegedExecutionRoute.ELEVATED_BROKER
        else:
            route = PrivilegedExecutionRoute.BLOCKED
        return PrivilegedExecutionRoutingDecision(
            route=route,
            reason_code=resolution.reason_code,
            resolution_digest=resolution.canonical_digest(),
        )
