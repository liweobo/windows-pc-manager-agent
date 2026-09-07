"""Sealed registry for high-level domain handoff and reconciliation boundaries."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.task_workflows import (
    DomainPreparationResult,
    DomainPreparationStatus,
    DomainReconciliationRequest,
    DomainReconciliationResult,
    DomainReconciliationStatus,
    DomainRecoverySummary,
    DomainType,
    DomainWorkflowRequest,
)


class DomainWorkflowError(RuntimeError):
    """Raised when a high-level domain is unavailable or violates the sealed API."""


class DomainWorkflow(Protocol):
    """High-level domain boundary; deliberately has no execute or confirm method."""

    @property
    def domain(self) -> DomainType:
        """Return the single domain owned by this adapter."""

    def prepare(self, request: DomainWorkflowRequest) -> DomainPreparationResult:
        """Prepare or open the owning domain's existing deterministic workflow."""

    def reconcile(self, request: DomainReconciliationRequest) -> DomainReconciliationResult:
        """Inspect current domain truth after interruption without replaying an action."""

    def recovery_summary(
        self,
        transaction_ref: str,
        rollback_level: RollbackLevel,
    ) -> DomainRecoverySummary:
        """Describe domain-owned recovery without exposing a global Undo operation."""


class DomainWorkflowRegistry:
    """Immutable, complete registry of the eleven Stage 5E high-level domains."""

    def __init__(self, workflows: Iterable[DomainWorkflow]) -> None:
        values = tuple(workflows)
        mapped = {workflow.domain: workflow for workflow in values}
        if len(mapped) != len(values):
            raise DomainWorkflowError("Duplicate high-level domain workflow")
        expected = set(DomainType)
        if set(mapped) != expected:
            missing = sorted(item.value for item in expected - set(mapped))
            extra = sorted(item.value for item in set(mapped) - expected)
            raise DomainWorkflowError(
                f"Incomplete domain registry; missing={missing}, extra={extra}"
            )
        self._workflows = mapped

    def require(self, domain: DomainType) -> DomainWorkflow:
        """Return one sealed high-level adapter."""
        try:
            return self._workflows[domain]
        except KeyError as exc:
            raise DomainWorkflowError("Unknown high-level domain") from exc

    def domains(self) -> tuple[DomainType, ...]:
        """Return registered domains in stable enum order."""
        return tuple(domain for domain in DomainType if domain in self._workflows)


class UserInterfaceHandoffWorkflow:
    """Production-safe UI handoff that never calls a low-level tool or action service."""

    def __init__(self, domain: DomainType) -> None:
        self._domain = domain

    @property
    def domain(self) -> DomainType:
        """Return the adapter's owning domain."""
        return self._domain

    def prepare(self, request: DomainWorkflowRequest) -> DomainPreparationResult:
        """Produce a reference-only instruction to enter the existing domain UI."""
        if request.domain is not self._domain:
            raise DomainWorkflowError("Domain handoff request reached the wrong adapter")
        return DomainPreparationResult(
            task_id=request.task_id,
            node_id=request.node_id,
            graph_version=request.graph_version,
            domain=request.domain,
            status=DomainPreparationStatus.READY_FOR_REVIEW,
            requires_domain_confirmation=False,
            risk_level=RiskLevel.R0,
            next_action_code=f"OPEN_{request.domain.value}_WORKFLOW",
        )

    def reconcile(self, request: DomainReconciliationRequest) -> DomainReconciliationResult:
        """Fail closed to a Fresh domain review because UI handoffs are not replayable."""
        if request.domain is not self._domain:
            raise DomainWorkflowError("Domain reconciliation reached the wrong adapter")
        return DomainReconciliationResult(
            reconciliation_id=request.reconciliation_id,
            task_id=request.task_id,
            node_id=request.node_id,
            domain=request.domain,
            status=DomainReconciliationStatus.REQUIRES_FRESH_PREPARATION,
            reason_code=f"{request.domain.value}_FRESH_DOMAIN_REVIEW_REQUIRED",
            requires_user_decision=True,
        )

    def recovery_summary(
        self,
        transaction_ref: str,
        rollback_level: RollbackLevel,
    ) -> DomainRecoverySummary:
        """Defer recovery truth to the owning domain and never claim automatic Undo."""
        if not transaction_ref:
            raise DomainWorkflowError("Recovery summary requires a domain transaction reference")
        return DomainRecoverySummary(
            domain=self._domain,
            transaction_ref=transaction_ref,
            rollback_level=rollback_level,
            recovery_available=rollback_level is not RollbackLevel.NONE,
            recovery_action_code=(
                f"OPEN_{self._domain.value}_RECOVERY"
                if rollback_level is not RollbackLevel.NONE
                else None
            ),
            reason_code=f"{self._domain.value}_DOMAIN_RECOVERY_REVIEW_REQUIRED",
        )


def build_default_domain_workflow_registry() -> DomainWorkflowRegistry:
    """Build the closed production registry using UI-only handoff adapters."""
    return DomainWorkflowRegistry(UserInterfaceHandoffWorkflow(domain) for domain in DomainType)
