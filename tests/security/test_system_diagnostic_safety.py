from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from tests.fixtures.system_diagnostics import build_registry

from pc_manager_agent.confirmation.external_data import ExternalDataConsentService
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.system_diagnostics import (
    DiagnosticIntent,
    DiagnosticIntentDraft,
    DiagnosticPlan,
    DiagnosticReport,
    SystemCollector,
    SystemSnapshot,
)
from pc_manager_agent.orchestration.diagnostic_provider import (
    DiagnosticExplainer,
    DiagnosticProviderPlanner,
)
from pc_manager_agent.orchestration.system_diagnostic_planner import DiagnosticPlanCompiler
from pc_manager_agent.providers.llm.base import (
    DiagnosticExplanationRequest,
    DiagnosticNarrativeDraft,
    DiagnosticNarrativeObservation,
    DiagnosticPlannerRequest,
    LLMProvider,
    PlannerRequest,
    ProviderDiagnosticIntentResult,
    ProviderDiagnosticNarrativeResult,
    ProviderPlanResult,
)
from pc_manager_agent.safety.system_diagnostics import DiagnosticSafetyValidator
from pc_manager_agent.tools.registry import ToolRegistry


class DiagnosticProviderFake(LLMProvider):
    """Capture minimal provider payloads and return finite structured output."""

    planner_request: DiagnosticPlannerRequest | None = None
    explanation_request: DiagnosticExplanationRequest | None = None
    narrative: DiagnosticNarrativeDraft = DiagnosticNarrativeDraft(observations=())

    @property
    def name(self) -> str:
        return "diagnostic-fake"

    async def create_plan(self, request: PlannerRequest) -> ProviderPlanResult:
        raise AssertionError(request)

    async def create_diagnostic_intent(
        self, request: DiagnosticPlannerRequest
    ) -> ProviderDiagnosticIntentResult:
        self.planner_request = request
        return ProviderDiagnosticIntentResult(
            intent=DiagnosticIntentDraft(
                intent=DiagnosticIntent.CPU,
                requested_collectors=(SystemCollector.CPU,),
            ),
            provider=self.name,
            request_id="trace",
        )

    async def explain_system_diagnostics(
        self, request: DiagnosticExplanationRequest
    ) -> ProviderDiagnosticNarrativeResult:
        self.explanation_request = request
        return ProviderDiagnosticNarrativeResult(
            narrative=self.narrative,
            provider=self.name,
        )


@pytest.mark.security
def test_safety_rejects_plan_field_mutations() -> None:
    registry = build_registry()
    validator = DiagnosticSafetyValidator(registry)
    plan = DiagnosticPlanCompiler(registry).compile(
        "cpu", DiagnosticPlanCompiler(registry).local_draft("cpu")
    )
    mutations = (
        plan.model_copy(update={"risk_level": RiskLevel.R1}),
        plan.model_copy(update={"rollback_level": RollbackLevel.FULL}),
        plan.model_copy(update={"estimated_system_changes": 1}),
        plan.model_copy(update={"requires_plan_confirmation": False}),
        plan.model_copy(update={"sample_count": 999}),
        plan.model_copy(update={"collectors": (SystemCollector.CPU, SystemCollector.CPU)}),
    )
    for changed in mutations:
        assert not validator.review(changed).approved


@pytest.mark.security
def test_safety_rejects_missing_invalid_and_misdeclared_tools() -> None:
    registry = build_registry()
    plan = DiagnosticPlanCompiler(registry).compile(
        "cpu", DiagnosticPlanCompiler(registry).local_draft("cpu")
    )
    missing_registry = ToolRegistry()
    assert not DiagnosticSafetyValidator(missing_registry).review(plan).approved

    tool = registry._tools[SystemCollector.CPU.value]  # test-only manifest mutation
    bad_registry = build_registry()
    bad_tool = bad_registry._tools[SystemCollector.CPU.value]
    bad_tool._manifest = replace(
        tool.manifest,
        risk_level=RiskLevel.R1,
        read_only=True,
        rollback_level=RollbackLevel.FULL,
        requires_confirmation=False,
    )
    review = DiagnosticSafetyValidator(bad_registry).review(plan)
    assert not review.approved
    codes = {issue.code for issue in review.issues}
    assert {"manifest-risk", "manifest-confirmation", "manifest-rollback"} <= codes


@pytest.mark.security
def test_provider_planner_sends_no_snapshot_and_requires_consent() -> None:
    registry = build_registry()
    provider = DiagnosticProviderFake()
    consent = ExternalDataConsentService()
    planner = DiagnosticProviderPlanner(provider, DiagnosticPlanCompiler(registry), consent)
    request = planner.build_request("diagnose cpu")
    payload = request.model_dump_json().casefold()
    assert "processes" in payload  # finite collector name only
    assert "command" not in payload
    approval = planner.request_consent("diagnose cpu")
    consent.resolve(approval.confirmation_id, True)
    result = asyncio.run(planner.plan("diagnose cpu", approval.confirmation_id))
    assert result.plan.collectors == (SystemCollector.SYSTEM_INFO, SystemCollector.CPU)


@pytest.mark.security
def test_explanation_payload_excludes_snapshot_values_and_process_names() -> None:
    provider = DiagnosticProviderFake()
    consent = ExternalDataConsentService()
    explainer = DiagnosticExplainer(provider, consent)
    plan = DiagnosticPlan(
        summary="cpu",
        user_goal="cpu",
        intent=DiagnosticIntent.CPU,
        collectors=(SystemCollector.SYSTEM_INFO, SystemCollector.CPU),
    )
    report = DiagnosticReport(
        plan_id=plan.plan_id,
        summary="done",
        snapshot=SystemSnapshot(outcomes=()),
        findings=(),
        thresholds={},
    )
    request = explainer.build_request(plan, report)
    assert "snapshot" not in request.model_dump_json().casefold()
    assert "process" not in request.model_dump_json().casefold()


@pytest.mark.security
def test_explanation_requires_consent_and_accepts_only_known_finding_codes() -> None:
    registry = build_registry()
    plan = DiagnosticPlanCompiler(registry).compile(
        "cpu", DiagnosticPlanCompiler(registry).local_draft("cpu")
    )
    from tests.fixtures.system_diagnostics import FakeSystemPlatform

    from pc_manager_agent.orchestration.diagnostic_engine import DiagnosticEngine
    from pc_manager_agent.tools.manifest import CancellationToken

    platform = FakeSystemPlatform()
    report = DiagnosticEngine().analyze(
        plan,
        SystemSnapshot(
            cpu=platform.collect_cpu(3, 0.1, CancellationToken()),
            outcomes=(),
        ),
    )
    provider = DiagnosticProviderFake()
    provider.narrative = DiagnosticNarrativeDraft(
        observations=(
            DiagnosticNarrativeObservation(
                finding_code="cpu.sustained-utilization",
                text="The measured window warrants observation before action",
            ),
        )
    )
    consent = ExternalDataConsentService()
    explainer = DiagnosticExplainer(provider, consent)
    approval = explainer.request_consent(plan, report)
    consent.resolve(approval.confirmation_id, True)
    result = asyncio.run(explainer.explain(plan, report, approval.confirmation_id))
    assert result == provider.narrative
    assert provider.explanation_request is not None

    provider.narrative = DiagnosticNarrativeDraft(
        observations=(
            DiagnosticNarrativeObservation(
                finding_code="invented.code",
                text="This reference was not supplied by deterministic code",
            ),
        )
    )
    second = explainer.request_consent(plan, report)
    consent.resolve(second.confirmation_id, True)
    with pytest.raises(ValueError, match="unknown finding"):
        asyncio.run(explainer.explain(plan, report, second.confirmation_id))


@pytest.mark.security
def test_diagnostic_narrative_rejects_numeric_claims() -> None:
    with pytest.raises(ValueError, match="numeric"):
        DiagnosticNarrativeObservation(finding_code="cpu.high", text="CPU reached 99 percent")
