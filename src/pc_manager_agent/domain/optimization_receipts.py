"""Reference-only domain result correlation, never an execution request."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, Field

from pc_manager_agent.domain.optimization_actions import (
    OptimizationOutcomeType,
    OptimizationTargetDomain,
)
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel


class OptimizationReceiptKind(StrEnum):
    """Finite existing transaction stores understood by the read-only receipt reader."""

    PROCESS = "PROCESS"
    STARTUP = "STARTUP"
    MSI = "MSI"
    VENDOR = "VENDOR"
    WINGET = "WINGET"
    MSIX = "MSIX"
    CLEANUP = "CLEANUP"
    RECYCLE_BIN = "RECYCLE_BIN"
    RESIDUAL = "RESIDUAL"
    FILES = "FILES"
    PERSONAL_TRASH = "PERSONAL_TRASH"

    @property
    def domain(self) -> OptimizationTargetDomain:
        """Map a persisted mechanism to its fixed owner, never an executable tool."""
        if self in {self.MSI, self.VENDOR, self.WINGET, self.MSIX}:
            return OptimizationTargetDomain.SOFTWARE
        if self in {self.CLEANUP, self.RECYCLE_BIN}:
            return OptimizationTargetDomain.SYSTEM_CLEANUP
        if self is self.RESIDUAL:
            return OptimizationTargetDomain.SOFTWARE_RESIDUAL
        if self in {self.FILES, self.PERSONAL_TRASH}:
            return OptimizationTargetDomain.PERSONAL_STORAGE
        return OptimizationTargetDomain(self.value)


class OptimizationTransactionReference(FrozenModel):
    """A UI may identify its transaction but cannot supply status or verification."""

    kind: OptimizationReceiptKind
    transaction_id: UUID


class DomainReceiptSnapshot(FrozenModel):
    """Privacy-minimized readback from a domain-owned durable transaction."""

    reference: OptimizationTransactionReference
    plan_id: UUID
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime
    updated_at: AwareDatetime
    state: str
    outcome: OptimizationOutcomeType | None = None
    confirmation_id: UUID | None = None
    risk: RiskLevel
    recovery: RollbackLevel
