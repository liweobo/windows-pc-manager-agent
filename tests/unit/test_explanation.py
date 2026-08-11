from __future__ import annotations

import asyncio
from pathlib import Path

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.confirmation.external_data import ExternalDataConsentService
from pc_manager_agent.domain.file_analysis import (
    AnalysisType,
    FileAnalysisIntentDraft,
    FileAnalysisSummary,
)
from pc_manager_agent.domain.reports import ScanStatus
from pc_manager_agent.orchestration.explanation import FileAnalysisExplainer
from pc_manager_agent.providers.llm.base import (
    AnalysisExplanationRequest,
    AnalysisNarrativeDraft,
    FileAnalysisPlannerRequest,
    LLMProvider,
    PlannerRequest,
    ProviderIntentResult,
    ProviderNarrativeResult,
    ProviderPlanResult,
)


class NarrativeProvider(LLMProvider):
    """Capture aggregate explanation payloads without a network call."""

    def __init__(self) -> None:
        self.request: AnalysisExplanationRequest | None = None

    @property
    def name(self) -> str:
        return "narrative-provider"

    async def create_plan(self, request: PlannerRequest) -> ProviderPlanResult:
        raise AssertionError(f"Unexpected legacy planner call: {request}")

    async def create_file_analysis_intent(
        self,
        request: FileAnalysisPlannerRequest,
    ) -> ProviderIntentResult:
        raise AssertionError(f"Unexpected file planner call: {request}")

    async def explain_file_analysis(
        self,
        request: AnalysisExplanationRequest,
    ) -> ProviderNarrativeResult:
        self.request = request
        return ProviderNarrativeResult(
            narrative=AnalysisNarrativeDraft(
                observations=("视频文件是主要候选类型",),
            ),
            provider=self.name,
            request_id="explain-1",
        )


def test_explainer_sends_aggregates_only_and_renders_numbers_locally(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "private-root"
    root.mkdir()
    record = runtime.authorized_paths.add_authorized(root)
    services = runtime.create_file_analysis_services()
    plan = services.compiler.compile(
        "analyze",
        FileAnalysisIntentDraft(
            authorized_root_ids=(record.path_id,),
            analyses=(AnalysisType.LARGE_FILES,),
        ),
    )
    summary = FileAnalysisSummary(
        files_scanned=12,
        directories_scanned=3,
        total_bytes=5_000,
        matching_files=2,
        matching_bytes=4_000,
        errors=1,
        status=ScanStatus.COMPLETED,
    )
    provider = NarrativeProvider()
    consent_service = ExternalDataConsentService()
    explainer = FileAnalysisExplainer(provider, consent_service)
    consent = explainer.request_external_consent(plan, summary)
    consent_service.resolve(consent.confirmation_id, True)

    rendered = asyncio.run(explainer.explain(plan, summary, consent.confirmation_id))

    assert provider.request is not None
    payload = provider.request.model_dump_json()
    assert str(root.resolve()) not in payload
    assert "12" in rendered
    assert "4,000" in rendered
    assert "视频文件是主要候选类型" in rendered
