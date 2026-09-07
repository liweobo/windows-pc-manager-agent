"""Domain-specific stale-state decisions for pause and crash recovery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import Field

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.task_workflows import DomainType


class StalenessKind(StrEnum):
    """Finite object lifetimes that require different recovery treatment."""

    PROCESS_IDENTITY = "PROCESS_IDENTITY"
    BROWSER_DOM = "BROWSER_DOM"
    FILE_IDENTITY = "FILE_IDENTITY"
    DOCUMENT_IDENTITY = "DOCUMENT_IDENTITY"
    STARTUP_STATE = "STARTUP_STATE"
    SERVICE_STATE = "SERVICE_STATE"
    SOFTWARE_INVENTORY = "SOFTWARE_INVENTORY"
    CLEANUP_CANDIDATE = "CLEANUP_CANDIDATE"
    OPTIMIZATION_REPORT = "OPTIMIZATION_REPORT"
    SYSTEM_OBSERVATION = "SYSTEM_OBSERVATION"


class StalenessDecision(StrEnum):
    """A stale decision only routes Fresh work; it never approves that work."""

    CURRENT_CONTEXT_ONLY = "CURRENT_CONTEXT_ONLY"
    REQUIRE_FRESH_RESOLUTION = "REQUIRE_FRESH_RESOLUTION"
    REQUIRE_FRESH_ANALYSIS = "REQUIRE_FRESH_ANALYSIS"
    INVALIDATE_REFERENCE = "INVALIDATE_REFERENCE"


class StalenessAssessment(FrozenModel):
    """Structured result used by Resume and crash recovery."""

    kind: StalenessKind
    domain: DomainType
    decision: StalenessDecision
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,99}$")
    observed_at: datetime
    reference_created_at: datetime


class TaskStalenessPolicy:
    """Use object-specific lifetimes instead of one unsafe global timeout."""

    _short_context = timedelta(seconds=60)
    _inventory_context = timedelta(minutes=5)

    def assess(
        self,
        kind: StalenessKind,
        domain: DomainType,
        reference_created_at: datetime,
        *,
        now: datetime | None = None,
        for_write: bool = False,
    ) -> StalenessAssessment:
        """Return the required Fresh action for one reference type."""
        observed = now or datetime.now(UTC)
        if kind in {StalenessKind.PROCESS_IDENTITY, StalenessKind.BROWSER_DOM}:
            decision = StalenessDecision.INVALIDATE_REFERENCE
            code = f"{kind.value}_IMMEDIATELY_STALE"
        elif kind in {
            StalenessKind.FILE_IDENTITY,
            StalenessKind.DOCUMENT_IDENTITY,
            StalenessKind.STARTUP_STATE,
            StalenessKind.SERVICE_STATE,
            StalenessKind.CLEANUP_CANDIDATE,
        }:
            decision = StalenessDecision.REQUIRE_FRESH_RESOLUTION
            code = f"{kind.value}_REQUIRES_FRESH_IDENTITY"
        elif kind is StalenessKind.OPTIMIZATION_REPORT:
            decision = StalenessDecision.REQUIRE_FRESH_ANALYSIS
            code = "OPTIMIZATION_REPORT_IS_CONTEXT_ONLY"
        elif for_write or observed - reference_created_at > self._inventory_context:
            decision = StalenessDecision.REQUIRE_FRESH_RESOLUTION
            code = f"{kind.value}_STALE"
        elif observed - reference_created_at > self._short_context:
            decision = StalenessDecision.REQUIRE_FRESH_ANALYSIS
            code = f"{kind.value}_REFRESH_RECOMMENDED"
        else:
            decision = StalenessDecision.CURRENT_CONTEXT_ONLY
            code = f"{kind.value}_CURRENT_CONTEXT_ONLY"
        return StalenessAssessment(
            kind=kind,
            domain=domain,
            decision=decision,
            reason_code=code,
            observed_at=observed,
            reference_created_at=reference_created_at,
        )
