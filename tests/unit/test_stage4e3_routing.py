"""Evidence typing, route validation, capability availability and conservative benefit tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pc_manager_agent.domain.optimization_actions import (
    OptimizationActionOutcome,
    OptimizationBenefitObservation,
    OptimizationCapability,
    OptimizationMetric,
    OptimizationOutcomeType,
    OptimizationRecommendationReference,
    OptimizationSession,
    OptimizationSessionItem,
    RecommendationActionability,
)
from pc_manager_agent.domain.system_optimization import (
    OptimizationConfidence,
    OptimizationRecommendation,
    PerformanceCategory,
    ProtectionLevel,
    RecommendationType,
)
from pc_manager_agent.orchestration.optimization_action_resolver import RecommendationActionResolver
from pc_manager_agent.orchestration.optimization_capabilities import (
    OptimizationDomainCapabilityRegistry,
)
from pc_manager_agent.safety.optimization_actions import (
    OptimizationActionPolicy,
    OptimizationRoutingError,
)
from tests.fixtures.optimization_actions import FakeDomainPreparation, build_stage4e3_report


@pytest.mark.parametrize("kind", tuple(RecommendationType))
def test_stage4e3_typed_recommendation_matrix(kind: RecommendationType) -> None:
    report = build_stage4e3_report(kind)
    route = RecommendationActionResolver(OptimizationActionPolicy()).resolve(
        report, report.recommendations[0].recommendation_id
    )
    assert route.causes_system_change is False
    assert (
        route.requires_confirmation
        and route.requires_fresh_resolution
        and route.requires_fresh_preview
    )
    if kind in {
        RecommendationType.MANUAL_REVIEW,
        RecommendationType.REVIEW_SERVICE,
        RecommendationType.FREE_DISK_SPACE,
    }:
        assert route.actionability is RecommendationActionability.REVIEW_ONLY
    elif kind is RecommendationType.NO_ACTION_NEEDED:
        assert route.actionability is RecommendationActionability.INFORMATIONAL
    else:
        assert route.actionability is RecommendationActionability.ROUTABLE
    assert "pid" not in type(route).model_fields
    assert "ProductCode" not in route.model_dump_json()


def test_stage4e3_legacy_text_never_selects_a_route() -> None:
    report = build_stage4e3_report(RecommendationType.REVIEW_STARTUP_ITEM)
    raw = report.recommendations[0].model_dump()
    raw.pop("recommendation_type")
    legacy = OptimizationRecommendation.model_validate(raw)
    report = report.model_copy(update={"recommendations": (legacy,)})
    route = RecommendationActionResolver(OptimizationActionPolicy()).resolve(
        report, legacy.recommendation_id
    )
    assert route.target_capability is OptimizationCapability.NONE
    assert route.actionability is RecommendationActionability.REVIEW_ONLY


@pytest.mark.parametrize(
    "changes",
    [
        {"path": "C:/private"},
        {"pid": 1},
        {"ProductCode": "fake"},
        {"domain": "admin_shell"},
        {"command": "bad"},
        {"use_admin": True},
        {"confirmation_id": str(uuid4())},
        {"force": True},
    ],
)
def test_stage4e3_reference_schema_rejects_route_injection(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        OptimizationRecommendationReference.model_validate(
            {
                "source_report_id": uuid4(),
                "recommendation_id": uuid4(),
                **changes,
            }
        )


def test_stage4e3_source_expiry_future_time_and_duplicate_ids() -> None:
    report = build_stage4e3_report(RecommendationType.REVIEW_STARTUP_ITEM)
    recommendation = report.recommendations[0]
    resolver = RecommendationActionResolver(OptimizationActionPolicy())
    for timestamp, reason in (
        (datetime.now(UTC) - timedelta(hours=1), "SOURCE_STALE"),
        (datetime.now(UTC) + timedelta(hours=1), "SOURCE_TIME_INVALID"),
        (datetime.now().replace(tzinfo=None), "SOURCE_TIME_INVALID"),
    ):
        with pytest.raises(OptimizationRoutingError, match=reason):
            resolver.resolve(
                report.model_copy(update={"generated_at": timestamp}),
                recommendation.recommendation_id,
            )
    with pytest.raises(OptimizationRoutingError, match="REFERENCE_INVALID"):
        resolver.resolve(report, uuid4())
    with pytest.raises(OptimizationRoutingError, match="REFERENCE_INVALID"):
        resolver.resolve(
            report.model_copy(update={"recommendations": (recommendation, recommendation)}),
            recommendation.recommendation_id,
        )
    with pytest.raises(ValueError):
        RecommendationActionResolver(OptimizationActionPolicy(), max_age_seconds=1801)


def test_stage4e3_evidence_references_cannot_be_cross_report_or_retyped() -> None:
    report = build_stage4e3_report(RecommendationType.REVIEW_STARTUP_ITEM)
    recommendation = report.recommendations[0]
    policy = OptimizationActionPolicy()
    for references in ((uuid4(),), recommendation.evidence_references * 2):
        with pytest.raises(OptimizationRoutingError, match="EVIDENCE_REFERENCE_INVALID"):
            policy.evaluate(
                report, recommendation.model_copy(update={"evidence_references": references})
            )
    result = policy.evaluate(
        report,
        recommendation.model_copy(
            update={"recommendation_type": RecommendationType.REVIEW_INSTALLED_SOFTWARE}
        ),
    )
    assert result.actionability is RecommendationActionability.BLOCKED
    result = policy.evaluate(report, recommendation.model_copy(update={"evidence_references": ()}))
    assert result.reason_codes == ("EVIDENCE_REQUIRED",)
    result = policy.evaluate(
        report,
        recommendation.model_copy(
            update={"recommendation_type": RecommendationType.NO_ACTION_NEEDED}
        ),
    )
    assert result.reason_codes == ("NO_ACTION_EVIDENCE_CONTRADICTED",)


def test_stage4e3_protection_and_source_allowlist() -> None:
    policy = OptimizationActionPolicy()
    report = build_stage4e3_report(RecommendationType.REVIEW_APPLICATION_CACHE)
    candidate = report.cleanup_candidates[0]
    protected = candidate.model_copy(update={"protection_level": ProtectionLevel.PROTECTED})
    assert (
        policy.evaluate(
            report.model_copy(update={"cleanup_candidates": (protected,)}),
            report.recommendations[0],
        ).actionability
        is RecommendationActionability.BLOCKED
    )
    unsupported = candidate.model_copy(update={"source": "browser-profile"})
    assert (
        policy.evaluate(
            report.model_copy(update={"cleanup_candidates": (unsupported,)}),
            report.recommendations[0],
        ).actionability
        is RecommendationActionability.NOT_CURRENTLY_SUPPORTED
    )
    assert (
        policy.evaluate(
            report,
            report.recommendations[0].model_copy(
                update={"recommendation_type": RecommendationType.REVIEW_LARGE_FILES}
            ),
        ).actionability
        is RecommendationActionability.BLOCKED
    )


def test_stage4e3_capability_registry_is_finite_and_sealed() -> None:
    registry = OptimizationDomainCapabilityRegistry()
    provider = FakeDomainPreparation(OptimizationCapability.STARTUP_REVIEW)
    with pytest.raises(OptimizationRoutingError, match="NOT_SEALED"):
        registry.available(provider.capability)
    registry.register(provider)
    with pytest.raises(OptimizationRoutingError, match="ALREADY_REGISTERED"):
        registry.register(provider)
    with pytest.raises(OptimizationRoutingError, match="NOT_ALLOWLISTED"):
        registry.register(FakeDomainPreparation(OptimizationCapability.NONE))
    registry.seal()
    assert registry.capabilities == (OptimizationCapability.STARTUP_REVIEW,)
    assert registry.available(provider.capability)
    assert not registry.available(OptimizationCapability.CLEANUP_REVIEW)
    with pytest.raises(OptimizationRoutingError, match="SEALED"):
        registry.register(provider)


def test_stage4e3_outcomes_and_benefits_cannot_invent_success() -> None:
    with pytest.raises(ValidationError):
        OptimizationActionOutcome(
            recommendation_id=uuid4(),
            target_domain=OptimizationCapability.STARTUP_REVIEW.domain,
            handoff_id=uuid4(),
            outcome=OptimizationOutcomeType.APPLIED_VERIFIED,
            verified=True,
            summary_code="OK",
        )
    with pytest.raises(ValidationError):
        OptimizationBenefitObservation(metric=OptimizationMetric.DISK_FREE_BYTES, measured=True)
    with pytest.raises(ValidationError):
        OptimizationBenefitObservation(
            metric=OptimizationMetric.DISK_FREE_BYTES,
            before=1,
            after=2,
            measured=True,
            attribution_confidence=OptimizationConfidence.HIGH,
        )
    observation = OptimizationBenefitObservation(
        metric=OptimizationMetric.DISK_FREE_BYTES,
        before=1,
        after=2,
        measured=True,
        attribution_confidence=OptimizationConfidence.LOW,
    )
    assert not observation.caused_by_action
    item = OptimizationSessionItem(recommendation_id=uuid4())
    with pytest.raises(ValidationError):
        OptimizationSession(source_report_id=uuid4(), items=(item, item))


def test_stage4e3_no_clear_bottleneck_does_not_hide_a_conflicting_finding() -> None:
    report = build_stage4e3_report(RecommendationType.NO_ACTION_NEEDED)
    finding = report.performance_findings[0].model_copy(
        update={"category": PerformanceCategory.CPU_PRESSURE}
    )
    decision = OptimizationActionPolicy().evaluate(
        report.model_copy(update={"performance_findings": (finding,)}), report.recommendations[0]
    )
    assert decision.actionability is RecommendationActionability.BLOCKED
