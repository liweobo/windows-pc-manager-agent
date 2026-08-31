"""Correlate one domain-owned Preview/result with one review, never with a global consent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Protocol
from uuid import UUID

from pc_manager_agent.domain.optimization_actions import (
    OptimizationActionOutcome,
    OptimizationActionPreparationResult,
    OptimizationCapability,
    OptimizationOutcomeType,
    OptimizationSessionStatus,
)
from pc_manager_agent.domain.optimization_receipts import (
    DomainReceiptSnapshot,
    OptimizationReceiptKind,
    OptimizationTransactionReference,
)
from pc_manager_agent.orchestration.optimization_handoffs import DomainReviewContext
from pc_manager_agent.orchestration.optimization_invalidation import (
    RecommendationInvalidationService,
)
from pc_manager_agent.orchestration.optimization_session import OptimizationSessionService
from pc_manager_agent.safety.optimization_actions import OptimizationRoutingError


class DomainResultReader(Protocol):
    """Read status from the domain, never from a window, LLM or caller's result body."""

    def read(self, reference: OptimizationTransactionReference) -> DomainReceiptSnapshot:
        """Return durable domain evidence for one reference."""
        ...


@dataclass(frozen=True, slots=True)
class _OutcomeBinding:
    session_id: UUID
    preparation: OptimizationActionPreparationResult
    context: DomainReviewContext
    initial: DomainReceiptSnapshot


class OptimizationOutcomeCoordinator:
    """Bind a newly prepared transaction before dispatch, then independently read its result."""

    def __init__(
        self,
        reader: DomainResultReader,
        sessions: OptimizationSessionService,
        invalidation: RecommendationInvalidationService,
    ) -> None:
        self._reader = reader
        self._sessions = sessions
        self._invalidation = invalidation
        self._bindings: dict[UUID, _OutcomeBinding] = {}
        self._transactions: set[UUID] = set()
        self._lock = RLock()

    def bind(
        self,
        session_id: UUID,
        preparation: OptimizationActionPreparationResult,
        context: DomainReviewContext,
        reference: OptimizationTransactionReference,
    ) -> None:
        """Observe one new domain Preview without approving it; refuse late/history attachment."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session.status in {
                OptimizationSessionStatus.CANCELLED,
                OptimizationSessionStatus.STALE,
            }:
                raise OptimizationRoutingError("OUTCOME_SESSION_CLOSED")
            if not any(
                item.handoff_id == preparation.handoff_id
                and item.recommendation_id == preparation.recommendation_id
                for item in session.items
            ):
                raise OptimizationRoutingError("OUTCOME_HANDOFF_MISMATCH")
            if (
                context.route.route_id != preparation.route_id
                or context.route.source_report_id != session.source_report_id
                or context.route.source_digest != session.source_digest
                or context.route.target_domain is not preparation.target_domain
                or reference.kind.domain is not preparation.target_domain
                or context.route.expires_at <= datetime.now(UTC)
            ):
                raise OptimizationRoutingError("OUTCOME_CONTEXT_MISMATCH_OR_EXPIRED")
            if (
                reference.kind is OptimizationReceiptKind.RECYCLE_BIN
                and context.route.target_capability is not OptimizationCapability.RECYCLE_BIN_REVIEW
            ):
                raise OptimizationRoutingError("BIN_EMPTY_REQUIRES_INDEPENDENT_REVIEW")
            if (
                reference.kind is OptimizationReceiptKind.CLEANUP
                and context.route.target_capability is not OptimizationCapability.CLEANUP_REVIEW
            ):
                raise OptimizationRoutingError("CLEANUP_REQUIRES_INDEPENDENT_REVIEW")
            if (
                reference.transaction_id in self._transactions
                or preparation.handoff_id in self._bindings
                or len(self._transactions) >= 1_000
            ):
                raise OptimizationRoutingError("OUTCOME_BINDING_REPLAY_OR_LIMIT")
            initial = self._reader.read(reference)
            if (
                initial.reference != reference
                or initial.created_at < context.created_at
                or initial.outcome is not None
                or initial.state
                not in {
                    "PLANNED",
                    "PREVIEWED",
                    "AWAITING_CONFIRMATION",
                    "AWAITING_PLAN_CONFIRMATION",
                }
            ):
                raise OptimizationRoutingError("OUTCOME_REQUIRES_NEW_UNDISPATCHED_PREVIEW")
            self._bindings[preparation.handoff_id] = _OutcomeBinding(
                session_id, preparation, context, initial
            )
            self._transactions.add(reference.transaction_id)

    def collect(self, handoff_id: UUID) -> OptimizationActionOutcome | None:
        """Record only domain evidence; None means still running/unknown, never success."""
        with self._lock:
            binding = self._bindings.get(handoff_id)
            if binding is None:
                return None
            current = self._reader.read(binding.initial.reference)
            if (
                current.reference != binding.initial.reference
                or current.plan_id != binding.initial.plan_id
                or current.plan_digest != binding.initial.plan_digest
                or current.created_at != binding.initial.created_at
                or current.risk != binding.initial.risk
                or current.recovery != binding.initial.recovery
            ):
                raise OptimizationRoutingError("OUTCOME_TRANSACTION_CHANGED")
            if current.outcome is None:
                return None
            applied = current.outcome in {
                OptimizationOutcomeType.APPLIED_VERIFIED,
                OptimizationOutcomeType.APPLIED_UNVERIFIED,
            }
            outcome = OptimizationActionOutcome(
                recommendation_id=binding.preparation.recommendation_id,
                target_domain=binding.preparation.target_domain,
                handoff_id=handoff_id,
                domain_plan_id=current.plan_id,
                domain_transaction_id=current.reference.transaction_id,
                domain_confirmation_id=current.confirmation_id,
                domain_risk=current.risk,
                recovery_level=current.recovery,
                outcome=current.outcome,
                verified=current.outcome is OptimizationOutcomeType.APPLIED_VERIFIED,
                user_cancelled=current.outcome is OptimizationOutcomeType.USER_CANCELLED,
                summary_code=current.state,
                completed_at=current.updated_at,
                authorization_source="domain_confirmation" if applied else None,
            )
            self._sessions.accept_correlated_outcome(binding.session_id, outcome)
            self._invalidation.invalidate_by_domain(outcome.target_domain)
            del self._bindings[handoff_id]
            return outcome
