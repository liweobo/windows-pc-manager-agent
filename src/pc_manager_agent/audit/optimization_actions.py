"""Aggregate-only Stage 4E3 routing and business-outcome correlation audit."""

from __future__ import annotations

from uuid import UUID

from pc_manager_agent import __version__
from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.domain.optimization_actions import (
    OptimizationActionOutcome,
    OptimizationActionPreparationResult,
    OptimizationActionRoute,
    OptimizationRecommendationReference,
)
from pc_manager_agent.domain.risk import RiskLevel


class OptimizationActionAuditLogger:
    """Record lineage IDs and finite decisions, never names, paths or confirmation secrets."""

    def __init__(self, repository: AuditRepository, git_commit: str | None = None) -> None:
        self._repository = repository
        self._git_commit = git_commit

    def route_decided(self, route: OptimizationActionRoute) -> None:
        """Record a review decision without representing it as execution authorization."""
        self._repository.record(
            AuditEvent(
                event_type="optimization.action.route_decided",
                risk_level=RiskLevel.R0,
                parameters={
                    "source_report_id": str(route.source_report_id),
                    "source_snapshot_id": str(route.source_snapshot_id),
                    "recommendation_id": str(route.recommendation_id),
                    "recommendation_type": route.recommendation_type.value,
                    "target_domain": route.target_domain.value,
                    "source_digest": route.source_digest,
                },
                result={
                    "actionability": route.actionability.value,
                    "reason_codes": list(route.route_reason_codes),
                    "execution_authorized": False,
                },
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )

    def prepared(
        self, route: OptimizationActionRoute, result: OptimizationActionPreparationResult
    ) -> None:
        """Record a reference-only domain handoff and its truthful preparation status."""
        self._repository.record(
            AuditEvent(
                event_type="optimization.action.prepared",
                risk_level=RiskLevel.R0,
                parameters={
                    "source_report_id": str(route.source_report_id),
                    "recommendation_id": str(route.recommendation_id),
                    "route_id": str(route.route_id),
                },
                result={
                    "handoff_id": str(result.handoff_id),
                    "status": result.status.value,
                    "fresh_domain_context_id": str(result.fresh_context_id)
                    if result.fresh_context_id
                    else None,
                    "target_domain": result.target_domain.value,
                    "execution_authorized": False,
                },
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )

    def rejected(self, reference: OptimizationRecommendationReference, reason: str) -> None:
        """Record only a finite reason, not exception text or untrusted recommendation text."""
        self._repository.record(
            AuditEvent(
                event_type="optimization.action.rejected",
                risk_level=RiskLevel.R0,
                parameters={
                    "source_report_id": str(reference.source_report_id),
                    "recommendation_id": str(reference.recommendation_id),
                },
                result={"reason_code": reason, "execution_authorized": False},
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )

    def outcome_recorded(self, session_id: UUID, outcome: OptimizationActionOutcome) -> None:
        """Correlate an independently checked business receipt; the domain is the authority."""
        self._repository.record(
            AuditEvent(
                event_type="optimization.action.outcome",
                risk_level=outcome.domain_risk or RiskLevel.R0,
                plan_id=str(outcome.domain_plan_id) if outcome.domain_plan_id else None,
                parameters={
                    "optimization_session_id": str(session_id),
                    "recommendation_id": str(outcome.recommendation_id),
                    "handoff_id": str(outcome.handoff_id),
                    "target_domain": outcome.target_domain.value,
                },
                result=outcome.model_dump(mode="json"),
                verification={
                    "verified": outcome.verified,
                    "authorization_source": outcome.authorization_source,
                },
                app_version=__version__,
                git_commit=self._git_commit,
            )
        )
