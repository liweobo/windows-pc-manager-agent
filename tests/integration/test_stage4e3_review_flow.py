"""Synthetic end-to-end preparation, confirmation isolation, journal and cancellation tests."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from tests.fixtures.optimization_actions import FakeDomainPreparation, build_stage4e3_report

from pc_manager_agent.audit.optimization_actions import OptimizationActionAuditLogger
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.domain.optimization_actions import (
    OptimizationCapability,
    OptimizationRecommendationReference,
    OptimizationSessionCreateRequest,
    OptimizationSessionStatus,
    RecommendationActionability,
    RecommendationActionState,
)
from pc_manager_agent.domain.system_optimization import RecommendationType, SystemOptimizationReport
from pc_manager_agent.orchestration.optimization_action_resolver import RecommendationActionResolver
from pc_manager_agent.orchestration.optimization_action_router import OptimizationActionRouter
from pc_manager_agent.orchestration.optimization_capabilities import (
    OptimizationDomainCapabilityRegistry,
)
from pc_manager_agent.orchestration.optimization_invalidation import (
    RecommendationInvalidationService,
)
from pc_manager_agent.orchestration.optimization_report_store import OptimizationReportSessionStore
from pc_manager_agent.orchestration.optimization_session import OptimizationSessionService
from pc_manager_agent.persistence.optimization_sessions import (
    OptimizationSessionRepository,
    OptimizationSessionStoreError,
)
from pc_manager_agent.safety.optimization_actions import (
    OptimizationActionPolicy,
    OptimizationRoutingError,
)
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolInputError, UnknownToolError
from pc_manager_agent.tools.system_tools.optimization_actions import (
    build_optimization_action_registry,
)


@dataclass
class Environment:
    path: Path
    audit: AuditRepository
    reports: OptimizationReportSessionStore
    report: SystemOptimizationReport
    provider: FakeDomainPreparation
    router: OptimizationActionRouter
    sessions: OptimizationSessionService
    repository: OptimizationSessionRepository
    invalidation: RecommendationInvalidationService

    @property
    def reference(self) -> OptimizationRecommendationReference:
        return OptimizationRecommendationReference(
            source_report_id=self.report.report_id,
            recommendation_id=self.report.recommendations[0].recommendation_id,
        )


@pytest.fixture
def environment(tmp_path: Path) -> Iterator[Environment]:
    path = tmp_path / "state.db"
    audit = AuditRepository(path)
    audit.initialize()
    repository = OptimizationSessionRepository(path)
    repository.initialize()
    reports = OptimizationReportSessionStore()
    report = build_stage4e3_report(RecommendationType.REVIEW_STARTUP_ITEM)
    recommendation = report.recommendations[0]
    report = report.model_copy(
        update={
            "recommendations": (
                recommendation,
                recommendation.model_copy(update={"recommendation_id": uuid4()}),
            )
        }
    )
    reports.save(report)
    provider = FakeDomainPreparation(OptimizationCapability.STARTUP_REVIEW)
    capabilities = OptimizationDomainCapabilityRegistry()
    capabilities.register(provider)
    capabilities.seal()
    invalidation = RecommendationInvalidationService()
    logger = OptimizationActionAuditLogger(audit)
    router = OptimizationActionRouter(
        reports,
        RecommendationActionResolver(OptimizationActionPolicy()),
        capabilities,
        invalidation,
        logger,
    )
    sessions = OptimizationSessionService(repository, router, logger)
    try:
        yield Environment(
            path, audit, reports, report, provider, router, sessions, repository, invalidation
        )
    finally:
        repository.close()
        audit.close()


def test_stage4e3_prepare_does_not_execute_or_authorize(environment: Environment) -> None:
    registry = build_optimization_action_registry(environment.router, environment.sessions)
    assert registry.names == (
        "optimization.recommendation.inspect",
        "optimization.recommendation.prepare_action",
        "optimization.session.create",
        "optimization.session.refresh",
    )
    for name in registry.names:
        assert registry.manifest(name).read_only
    result = registry.execute(
        "optimization.recommendation.prepare_action", environment.reference.model_dump(mode="json")
    )
    assert result.model_dump()["causes_system_change"] is False
    assert environment.provider.calls == 1
    with pytest.raises(UnknownToolError):
        registry.execute("optimization.apply_all", {})
    with pytest.raises(ToolInputError):
        registry.execute(
            "optimization.recommendation.prepare_action",
            {
                **environment.reference.model_dump(mode="json"),
                "confirmation_id": str(uuid4()),
            },
        )
    events = environment.audit.list_recent(10)
    assert not any("IGNORE SAFETY" in str(row.parameters) for row in events)
    assert all(
        row.result is None or not row.result.get("execution_authorized", False) for row in events
    )


def test_stage4e3_old_report_and_unavailable_domain_fail_closed(environment: Environment) -> None:
    environment.provider.supported = False
    assert (
        environment.router.inspect(environment.reference).actionability
        is RecommendationActionability.NOT_CURRENTLY_SUPPORTED
    )
    with pytest.raises(OptimizationRoutingError, match="NOT_PREPARABLE"):
        environment.router.prepare(environment.reference)
    environment.reports.clear()
    with pytest.raises(OptimizationRoutingError, match="SOURCE_REPORT_UNAVAILABLE"):
        environment.router.inspect(environment.reference)
    assert environment.provider.calls == 0


def test_stage4e3_domain_change_and_cancel_invalidate_preparation(environment: Environment) -> None:
    token = CancellationToken()
    token.cancel()
    with pytest.raises(OptimizationRoutingError, match="PREPARATION_CANCELLED"):
        environment.router.prepare(environment.reference, token)
    environment.invalidation.invalidate_by_domain(OptimizationCapability.STARTUP_REVIEW.domain)
    with pytest.raises(OptimizationRoutingError, match="STALE"):
        environment.router.prepare(environment.reference)
    assert environment.provider.calls == 0


def test_stage4e3_session_is_sequential_and_cancel_is_not_undo(environment: Environment) -> None:
    ids = tuple(item.recommendation_id for item in environment.report.recommendations)
    session = environment.sessions.create(
        OptimizationSessionCreateRequest(
            source_report_id=environment.report.report_id,
            recommendation_ids=ids,
        )
    )
    handoff = environment.sessions.prepare(session.session_id, ids[0])
    with pytest.raises(OptimizationRoutingError, match="ALREADY_ACTIVE"):
        environment.sessions.prepare(session.session_id, ids[1])
    cancelled = environment.sessions.cancel(session.session_id)
    assert cancelled.status is OptimizationSessionStatus.CANCELLED
    assert cancelled.items[0].state is RecommendationActionState.ROUTED
    assert cancelled.items[1].state is RecommendationActionState.SKIPPED
    assert not cancelled.global_authorization and not cancelled.global_undo_available
    with pytest.raises(OptimizationRoutingError, match="NOT_OPEN"):
        environment.sessions.prepare(session.session_id, ids[1])
    closed = environment.sessions.close_review(session.session_id, ids[0], handoff.handoff_id)
    assert closed.status is OptimizationSessionStatus.CANCELLED
    assert closed.items[0].outcome is None


def test_stage4e3_restart_marks_session_stale_without_dispatch(environment: Environment) -> None:
    session = environment.sessions.create(
        OptimizationSessionCreateRequest(
            source_report_id=environment.report.report_id,
            recommendation_ids=(environment.reference.recommendation_id,),
        )
    )
    other = OptimizationSessionRepository(environment.path)
    try:
        assert other.initialize() == (session.session_id,)
        assert other.get(session.session_id).status is OptimizationSessionStatus.STALE
        assert environment.provider.calls == 0
    finally:
        other.close()


def test_stage4e3_journal_rejects_concurrent_overwrite(environment: Environment) -> None:
    ids = tuple(item.recommendation_id for item in environment.report.recommendations)
    session = environment.sessions.create(
        OptimizationSessionCreateRequest(
            source_report_id=environment.report.report_id,
            recommendation_ids=ids,
        )
    )
    changed = environment.sessions.skip(session.session_id, ids[0])
    assert changed.revision == 2
    with pytest.raises(OptimizationSessionStoreError, match="concurrently"):
        environment.repository.save(changed, expected_revision=1)
    assert environment.sessions.get(session.session_id) == changed
