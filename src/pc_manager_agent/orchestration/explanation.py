"""Consent-gated aggregate explanation with deterministic numeric rendering."""

from __future__ import annotations

from uuid import UUID

from pc_manager_agent.confirmation.external_data import (
    ExternalDataConsentRequest,
    ExternalDataConsentService,
    ExternalDataPurpose,
)
from pc_manager_agent.domain.file_analysis import FileAnalysisPlan, FileAnalysisSummary
from pc_manager_agent.providers.llm.base import (
    AnalysisExplanationRequest,
    AnalysisNarrativeDraft,
    LLMProvider,
)


class FileAnalysisExplainer:
    """Send aggregate-only results after consent and render measured numbers locally."""

    def __init__(
        self,
        provider: LLMProvider,
        external_consent: ExternalDataConsentService,
    ) -> None:
        self._provider = provider
        self._external_consent = external_consent

    def build_request(
        self,
        plan: FileAnalysisPlan,
        summary: FileAnalysisSummary,
    ) -> AnalysisExplanationRequest:
        """Build a payload containing aggregates and thresholds but no paths."""
        return AnalysisExplanationRequest(
            summary=summary,
            filters=plan.filters,
            analyses=plan.analyses,
        )

    def request_external_consent(
        self,
        plan: FileAnalysisPlan,
        summary: FileAnalysisSummary,
    ) -> ExternalDataConsentRequest:
        """Request approval for one exact aggregate explanation payload."""
        request = self.build_request(plan, summary)
        return self._external_consent.request(
            purpose=ExternalDataPurpose.EXPLANATION,
            provider=self._provider.name,
            payload=request.model_dump(mode="json"),
            object_summary=(
                "将把文件数量、总大小、候选数量、阈值和分类统计发送给模型；"
                "不会发送路径、文件名或文件内容。"
            ),
        )

    async def explain(
        self,
        plan: FileAnalysisPlan,
        summary: FileAnalysisSummary,
        confirmation_id: UUID,
    ) -> str:
        """Require consent, call the provider, and render all numbers locally."""
        request = self.build_request(plan, summary)
        self._external_consent.require_approved(
            confirmation_id,
            purpose=ExternalDataPurpose.EXPLANATION,
            provider=self._provider.name,
            payload=request.model_dump(mode="json"),
        )
        result = await self._provider.explain_file_analysis(request)
        return render_analysis_explanation(summary, result.narrative)


def render_analysis_explanation(
    summary: FileAnalysisSummary,
    narrative: AnalysisNarrativeDraft | None = None,
) -> str:
    """Render measured values verbatim and append optional number-free observations."""
    lines = [
        f"扫描文件：{summary.files_scanned:,}",
        f"扫描目录：{summary.directories_scanned:,}",
        f"扫描总量：{summary.total_bytes:,} 字节",
        f"符合计划条件：{summary.matching_files:,}",
        f"候选文件总量：{summary.matching_bytes:,} 字节",
        f"跳过或错误：{summary.errors:,}",
    ]
    if narrative is not None:
        lines.append("")
        lines.append("模型基于聚合数据的定性说明：")
        lines.extend(f"- {item}" for item in narrative.observations)
    return "\n".join(lines)
