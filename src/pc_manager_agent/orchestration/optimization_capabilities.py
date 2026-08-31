"""Finite registry of preparation interfaces, not business executors."""

from __future__ import annotations

from typing import Protocol

from pc_manager_agent.domain.optimization_actions import (
    OptimizationActionPreparationResult,
    OptimizationActionRoute,
    OptimizationCapability,
    OptimizationUISurface,
    PreparationStatus,
)
from pc_manager_agent.domain.system_optimization import SystemOptimizationReport
from pc_manager_agent.safety.optimization_actions import OptimizationRoutingError
from pc_manager_agent.tools.manifest import CancellationToken


class DomainPreparationService(Protocol):
    """Narrow business review interface; no execute/confirm/elevate method is exposed."""

    @property
    def capability(self) -> OptimizationCapability:
        """Return one fixed review capability."""
        ...

    def available(self) -> bool:
        """Report actual availability without launching or changing system state."""
        ...

    def prepare(
        self,
        route: OptimizationActionRoute,
        report: SystemOptimizationReport,
        cancellation: CancellationToken,
    ) -> OptimizationActionPreparationResult:
        """Prepare read-only review context; mutations still belong to the original domain."""
        ...


def capability_surface(capability: OptimizationCapability) -> OptimizationUISurface:
    """Resolve an immutable UI destination without accepting arbitrary page names."""
    surfaces = {
        OptimizationCapability.PROCESS_REVIEW: OptimizationUISurface.PROCESS,
        OptimizationCapability.STARTUP_REVIEW: OptimizationUISurface.STARTUP,
        OptimizationCapability.SERVICE_REVIEW: OptimizationUISurface.SERVICE_READONLY,
        OptimizationCapability.SOFTWARE_REVIEW: OptimizationUISurface.SOFTWARE,
        OptimizationCapability.RESIDUAL_REVIEW: OptimizationUISurface.RESIDUAL,
        OptimizationCapability.CLEANUP_REVIEW: OptimizationUISurface.CLEANUP,
        OptimizationCapability.RECYCLE_BIN_REVIEW: OptimizationUISurface.RECYCLE_BIN,
        OptimizationCapability.PERSONAL_STORAGE_REVIEW: OptimizationUISurface.PERSONAL_STORAGE,
        OptimizationCapability.STORAGE_OVERVIEW: OptimizationUISurface.OVERVIEW,
    }
    try:
        return surfaces[capability]
    except KeyError as exc:
        raise OptimizationRoutingError("CAPABILITY_HAS_NO_SURFACE") from exc


class OptimizationDomainCapabilityRegistry:
    """Seal a finite preparation-only dependency map after application composition."""

    def __init__(self) -> None:
        self._services: dict[OptimizationCapability, DomainPreparationService] = {}
        self._sealed = False

    def register(self, service: DomainPreparationService) -> None:
        """Reject duplicate, unknown and post-composition registrations."""
        capability = service.capability
        if self._sealed:
            raise OptimizationRoutingError("CAPABILITY_REGISTRY_SEALED")
        if (
            not isinstance(capability, OptimizationCapability)
            or capability is OptimizationCapability.NONE
        ):
            raise OptimizationRoutingError("CAPABILITY_NOT_ALLOWLISTED")
        if capability in self._services:
            raise OptimizationRoutingError("CAPABILITY_ALREADY_REGISTERED")
        self._services[capability] = service

    def seal(self) -> None:
        """Prevent later runtime injection of a different preparation implementation."""
        self._sealed = True

    @property
    def capabilities(self) -> tuple[OptimizationCapability, ...]:
        """Return the declared capability set in canonical order."""
        return tuple(item for item in OptimizationCapability if item in self._services)

    def available(self, capability: OptimizationCapability) -> bool:
        """Return false for a missing capability; never select a fallback executor."""
        if not self._sealed:
            raise OptimizationRoutingError("CAPABILITY_REGISTRY_NOT_SEALED")
        service = self._services.get(capability)
        return service is not None and service.available()

    def prepare(
        self,
        route: OptimizationActionRoute,
        report: SystemOptimizationReport,
        cancellation: CancellationToken,
    ) -> OptimizationActionPreparationResult:
        """Validate the preparation result and reject domain/surface/identity substitution."""
        if not self.available(route.target_capability):
            raise OptimizationRoutingError("CAPABILITY_UNAVAILABLE")
        if cancellation.is_cancelled:
            raise OptimizationRoutingError("PREPARATION_CANCELLED")
        service = self._services[route.target_capability]
        result = service.prepare(route, report, cancellation)
        result = OptimizationActionPreparationResult.model_validate_json(result.model_dump_json())
        if (
            result.route_id != route.route_id
            or result.recommendation_id != route.recommendation_id
            or result.target_domain is not route.target_domain
            or (
                result.next_ui_surface is not None
                and result.next_ui_surface is not capability_surface(route.target_capability)
            )
        ):
            raise OptimizationRoutingError("DOMAIN_PREPARATION_MISMATCH")
        if result.status in {
            PreparationStatus.READY_FOR_REVIEW,
            PreparationStatus.NEEDS_TARGET_SELECTION,
        } and (result.fresh_context_id is None or result.next_ui_surface is None):
            raise OptimizationRoutingError("DOMAIN_PREPARATION_INCOMPLETE")
        if cancellation.is_cancelled:
            raise OptimizationRoutingError("PREPARATION_CANCELLED")
        return result
