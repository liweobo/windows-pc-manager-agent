"""Canonical local recommendation resolution without natural-language authority."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pc_manager_agent.domain.optimization_actions import OptimizationActionRoute
from pc_manager_agent.domain.system_optimization import SystemOptimizationReport
from pc_manager_agent.safety.optimization_actions import (
    OptimizationActionPolicy,
    OptimizationRoutingError,
)


def optimization_source_digest(report: SystemOptimizationReport) -> str:
    """Bind all evidence and provenance, not just a mutable title or a UUID."""
    payload = json.dumps(report.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RecommendationActionResolver:
    """Produce only fixed review routes from a current, locally loaded report."""

    def __init__(
        self,
        policy: OptimizationActionPolicy,
        *,
        max_age_seconds: int = 1800,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not 1 <= max_age_seconds <= 1800:
            raise ValueError("Recommendation age must be within the session report lifetime")
        self._policy = policy
        self._age = timedelta(seconds=max_age_seconds)
        self._now = now or (lambda: datetime.now(UTC))

    def resolve(
        self, report: SystemOptimizationReport, recommendation_id: UUID
    ) -> OptimizationActionRoute:
        """Check source age and uniqueness, then independently validate semantic evidence."""
        report = SystemOptimizationReport.model_validate_json(report.model_dump_json())
        current = self._now()
        times = (
            report.generated_at,
            report.snapshot.collected_at,
            report.snapshot.system.collected_at,
        )
        if any(value.tzinfo is None or value.utcoffset() is None for value in times):
            raise OptimizationRoutingError("SOURCE_TIME_INVALID")
        if any(value > current + timedelta(seconds=5) for value in times):
            raise OptimizationRoutingError("SOURCE_TIME_INVALID")
        expiry = min(times) + self._age
        if current >= expiry:
            raise OptimizationRoutingError("SOURCE_STALE")
        ids = tuple(item.recommendation_id for item in report.recommendations)
        if len(ids) != len(set(ids)) or ids.count(recommendation_id) != 1:
            raise OptimizationRoutingError("RECOMMENDATION_REFERENCE_INVALID")
        recommendation = next(
            item for item in report.recommendations if item.recommendation_id == recommendation_id
        )
        decision = self._policy.evaluate(report, recommendation)
        return OptimizationActionRoute(
            source_report_id=report.report_id,
            source_snapshot_id=report.snapshot.snapshot_id,
            recommendation_id=recommendation_id,
            recommendation_type=recommendation.recommendation_type,
            source_digest=optimization_source_digest(report),
            target_domain=decision.capability.domain,
            target_capability=decision.capability,
            actionability=decision.actionability,
            evidence_references=recommendation.evidence_references,
            route_reason_codes=decision.reason_codes,
            expires_at=expiry,
        )
