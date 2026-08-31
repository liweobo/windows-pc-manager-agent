"""Sequential review-container state, independent of every domain transaction."""

from __future__ import annotations

from threading import RLock
from uuid import UUID

from pc_manager_agent.audit.optimization_actions import OptimizationActionAuditLogger
from pc_manager_agent.domain.optimization_actions import (
    OptimizationActionOutcome,
    OptimizationActionPreparationResult,
    OptimizationOutcomeType,
    OptimizationRecommendationReference,
    OptimizationSession,
    OptimizationSessionCreateRequest,
    OptimizationSessionItem,
    OptimizationSessionStatus,
    PreparationStatus,
    RecommendationActionState,
)
from pc_manager_agent.orchestration.optimization_action_router import OptimizationActionRouter
from pc_manager_agent.persistence.optimization_sessions import OptimizationSessionRepository
from pc_manager_agent.safety.optimization_actions import OptimizationRoutingError
from pc_manager_agent.tools.manifest import CancellationToken


class OptimizationSessionService:
    """Organize reviews without issuing business confirmations or dispatching writes."""

    def __init__(
        self,
        repository: OptimizationSessionRepository,
        router: OptimizationActionRouter,
        audit: OptimizationActionAuditLogger,
    ) -> None:
        self._repository = repository
        self._router = router
        self._audit = audit
        self._tokens: dict[UUID, CancellationToken] = {}
        self._lock = RLock()

    def create(self, request: OptimizationSessionCreateRequest) -> OptimizationSession:
        """Validate every local reference and create only a review checklist."""
        if len(set(request.recommendation_ids)) != len(request.recommendation_ids):
            raise OptimizationRoutingError("DUPLICATE_SESSION_RECOMMENDATION")
        routes = tuple(
            self._router.inspect(
                OptimizationRecommendationReference(
                    source_report_id=request.source_report_id,
                    recommendation_id=item,
                )
            )
            for item in request.recommendation_ids
        )
        if len({item.source_digest for item in routes}) != 1:
            raise OptimizationRoutingError("SESSION_SOURCE_CHANGED")
        session = OptimizationSession(
            source_report_id=request.source_report_id,
            source_snapshot_id=routes[0].source_snapshot_id,
            source_digest=routes[0].source_digest,
            items=tuple(
                OptimizationSessionItem(recommendation_id=item)
                for item in request.recommendation_ids
            ),
        )
        self._repository.create(session)
        return session

    def get(self, session_id: UUID) -> OptimizationSession:
        """Read the durable summary; it is not executable even after restart."""
        return self._repository.get(session_id)

    def prepare(
        self, session_id: UUID, recommendation_id: UUID
    ) -> OptimizationActionPreparationResult:
        """Reserve one review then release the lock while its cancellable preparation runs."""
        with self._lock:
            session = self.get(session_id)
            self._require_open(session)
            item = self._item(session, recommendation_id)
            if item.state is not RecommendationActionState.PENDING or any(
                value.state is RecommendationActionState.ROUTED for value in session.items
            ):
                raise OptimizationRoutingError("SESSION_REVIEW_ALREADY_ACTIVE_OR_FINISHED")
            reference = OptimizationRecommendationReference(
                source_report_id=session.source_report_id,
                recommendation_id=recommendation_id,
            )
            try:
                route = self._router.inspect(reference)
            except OptimizationRoutingError:
                self._replace(
                    session, item.model_copy(update={"state": RecommendationActionState.STALE})
                )
                raise
            if route.source_digest != session.source_digest:
                raise OptimizationRoutingError("SESSION_SOURCE_CHANGED")
            token = CancellationToken()
            self._replace(
                session, item.model_copy(update={"state": RecommendationActionState.ROUTED})
            )
            self._tokens[session_id] = token
        try:
            result = self._router.prepare(reference, token)
        except Exception:
            with self._lock:
                self._tokens.pop(session_id, None)
                current = self.get(session_id)
                pending = self._item(current, recommendation_id)
                if current.status is not OptimizationSessionStatus.CANCELLED:
                    self._replace(
                        current,
                        pending.model_copy(update={"state": RecommendationActionState.FAILED}),
                    )
            raise
        with self._lock:
            self._tokens.pop(session_id, None)
            current = self.get(session_id)
            if current.status is OptimizationSessionStatus.CANCELLED or token.is_cancelled:
                raise OptimizationRoutingError("SESSION_CANCELLED")
            state = {
                PreparationStatus.BLOCKED: RecommendationActionState.BLOCKED,
                PreparationStatus.STALE: RecommendationActionState.STALE,
                PreparationStatus.UNSUPPORTED: RecommendationActionState.BLOCKED,
                PreparationStatus.FAILED: RecommendationActionState.FAILED,
                PreparationStatus.NO_LONGER_APPLICABLE: (
                    RecommendationActionState.NO_LONGER_APPLICABLE
                ),
            }.get(result.status, RecommendationActionState.ROUTED)
            self._replace(
                current, item.model_copy(update={"state": state, "handoff_id": result.handoff_id})
            )
        return result

    def skip(self, session_id: UUID, recommendation_id: UUID) -> OptimizationSession:
        """Skip a not-yet-dispatched review; completed outcomes can never be overwritten."""
        with self._lock:
            session = self.get(session_id)
            self._require_open(session)
            item = self._item(session, recommendation_id)
            if item.state not in {
                RecommendationActionState.PENDING,
                RecommendationActionState.REVIEWED,
            }:
                raise OptimizationRoutingError("SESSION_ITEM_CANNOT_BE_SKIPPED")
            return self._replace(
                session, item.model_copy(update={"state": RecommendationActionState.SKIPPED})
            )

    def close_review(
        self, session_id: UUID, recommendation_id: UUID, handoff_id: UUID
    ) -> OptimizationSession:
        """Mark navigation-only review finished; closing a window never means a write succeeded."""
        with self._lock:
            session = self.get(session_id)
            item = self._item(session, recommendation_id)
            if item.state is not RecommendationActionState.ROUTED or item.handoff_id != handoff_id:
                raise OptimizationRoutingError("SESSION_HANDOFF_MISMATCH")
            return self._replace(
                session, item.model_copy(update={"state": RecommendationActionState.REVIEWED})
            )

    def cancel(self, session_id: UUID) -> OptimizationSession:
        """Stop future preparations only; active domain work and completed results stay truthful."""
        with self._lock:
            session = self.get(session_id)
            if session.status in {
                OptimizationSessionStatus.COMPLETED,
                OptimizationSessionStatus.STALE,
            }:
                return session
            token = self._tokens.get(session_id)
            if token is not None:
                token.cancel()
            items = tuple(
                item.model_copy(update={"state": RecommendationActionState.SKIPPED})
                if item.state is RecommendationActionState.PENDING
                or (item.state is RecommendationActionState.ROUTED and item.handoff_id is None)
                else item
                for item in session.items
            )
            changed = session.model_copy(
                update={
                    "revision": session.revision + 1,
                    "status": OptimizationSessionStatus.CANCELLED,
                    "items": items,
                }
            )
            self._repository.save(changed, expected_revision=session.revision)
            return changed

    def accept_correlated_outcome(
        self, session_id: UUID, outcome: OptimizationActionOutcome
    ) -> OptimizationSession:
        """Internal receipt-sink used only after the domain result reader verifies lineage.

        This method is intentionally absent from all tools, planner schemas and UI requests.
        """
        with self._lock:
            session = self.get(session_id)
            item = self._item(session, outcome.recommendation_id)
            if item.handoff_id != outcome.handoff_id or item.outcome is not None:
                raise OptimizationRoutingError("OUTCOME_HANDOFF_MISMATCH_OR_REPLAY")
            if item.state not in {
                RecommendationActionState.ROUTED,
                RecommendationActionState.REVIEWED,
            }:
                raise OptimizationRoutingError("OUTCOME_ITEM_NOT_ACTIVE")
            self._audit.outcome_recorded(session_id, outcome)
            state = {
                OptimizationOutcomeType.APPLIED_VERIFIED: RecommendationActionState.EXECUTED,
                OptimizationOutcomeType.APPLIED_UNVERIFIED: RecommendationActionState.EXECUTED,
                OptimizationOutcomeType.BLOCKED: RecommendationActionState.BLOCKED,
                OptimizationOutcomeType.NO_LONGER_APPLICABLE: (
                    RecommendationActionState.NO_LONGER_APPLICABLE
                ),
                OptimizationOutcomeType.USER_CANCELLED: RecommendationActionState.SKIPPED,
                OptimizationOutcomeType.FAILED: RecommendationActionState.FAILED,
                OptimizationOutcomeType.SKIPPED: RecommendationActionState.SKIPPED,
            }[outcome.outcome]
            return self._replace(
                session, item.model_copy(update={"state": state, "outcome": outcome})
            )

    def _replace(
        self, session: OptimizationSession, item: OptimizationSessionItem
    ) -> OptimizationSession:
        items = tuple(
            item if value.recommendation_id == item.recommendation_id else value
            for value in session.items
        )
        pending = any(
            value.state in {RecommendationActionState.PENDING, RecommendationActionState.ROUTED}
            for value in items
        )
        applied = any(value.state is RecommendationActionState.EXECUTED for value in items)
        status = session.status
        if status not in {OptimizationSessionStatus.CANCELLED, OptimizationSessionStatus.STALE}:
            status = (
                OptimizationSessionStatus.PARTIALLY_APPLIED
                if applied and pending
                else OptimizationSessionStatus.IN_PROGRESS
                if pending
                else OptimizationSessionStatus.COMPLETED
            )
        changed = session.model_copy(
            update={"revision": session.revision + 1, "items": items, "status": status}
        )
        self._repository.save(changed, expected_revision=session.revision)
        return changed

    @staticmethod
    def _item(session: OptimizationSession, recommendation_id: UUID) -> OptimizationSessionItem:
        for item in session.items:
            if item.recommendation_id == recommendation_id:
                return item
        raise OptimizationRoutingError("RECOMMENDATION_NOT_IN_SESSION")

    @staticmethod
    def _require_open(session: OptimizationSession) -> None:
        if session.status in {
            OptimizationSessionStatus.CANCELLED,
            OptimizationSessionStatus.STALE,
            OptimizationSessionStatus.COMPLETED,
        }:
            raise OptimizationRoutingError("SESSION_NOT_OPEN")
