"""Consent-gated provider planning and explanation for Stage 3 diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pc_manager_agent.confirmation.external_data import (
    ExternalDataConsentRequest,
    ExternalDataConsentService,
    ExternalDataPurpose,
)
from pc_manager_agent.domain.system_diagnostics import DiagnosticPlan, DiagnosticReport
from pc_manager_agent.orchestration.system_diagnostic_planner import DiagnosticPlanCompiler
from pc_manager_agent.providers.llm.base import (
    DiagnosticExplanationFinding,
    DiagnosticExplanationRequest,
    DiagnosticNarrativeDraft,
    DiagnosticPlannerRequest,
    LLMProvider,
)


@dataclass(frozen=True, slots=True)
class DiagnosticProviderPlanningResult:
    """Locally compiled plan and provider trace metadata."""

    plan: DiagnosticPlan
    provider: str
    provider_request_id: str | None


class DiagnosticProviderPlanner:
    """Ask a provider for finite intent only, then compile under local policy."""

    def __init__(
        self,
        provider: LLMProvider,
        compiler: DiagnosticPlanCompiler,
        consent: ExternalDataConsentService,
    ) -> None:
        self._provider = provider
        self._compiler = compiler
        self._consent = consent

    def build_request(self, user_goal: str) -> DiagnosticPlannerRequest:
        """Build a goal-only request containing the complete finite local allow-list."""
        from pc_manager_agent.domain.system_diagnostics import DiagnosticIntent, SystemCollector

        return DiagnosticPlannerRequest(
            user_goal=user_goal,
            allowed_intents=tuple(DiagnosticIntent),
            allowed_collectors=tuple(SystemCollector),
        )

    def request_consent(self, user_goal: str) -> ExternalDataConsentRequest:
        """Request exact approval before sending the user goal to a provider."""
        request = self.build_request(user_goal)
        return self._consent.request(
            purpose=ExternalDataPurpose.PLANNING,
            provider=self._provider.name,
            payload=request.model_dump(mode="json"),
            object_summary=(
                "Send only your diagnostic goal and the fixed R0 collector names. "
                "No process, service, software, path, command, or measurement is included."
            ),
        )

    async def plan(self, user_goal: str, confirmation_id: UUID) -> DiagnosticProviderPlanningResult:
        """Call the provider only after exact consent and compile its untrusted draft."""
        request = self.build_request(user_goal)
        payload = request.model_dump(mode="json")
        self._consent.require_approved(
            confirmation_id,
            purpose=ExternalDataPurpose.PLANNING,
            provider=self._provider.name,
            payload=payload,
        )
        result = await self._provider.create_diagnostic_intent(request)
        return DiagnosticProviderPlanningResult(
            plan=self._compiler.compile(user_goal, result.intent),
            provider=result.provider,
            provider_request_id=result.request_id,
        )


class DiagnosticExplainer:
    """Offer a path-free, process-free finding summary to a provider after consent."""

    def __init__(self, provider: LLMProvider, consent: ExternalDataConsentService) -> None:
        self._provider = provider
        self._consent = consent

    @staticmethod
    def build_request(
        plan: DiagnosticPlan, report: DiagnosticReport
    ) -> DiagnosticExplanationRequest:
        """Remove measured values and local identities from an explanation payload."""
        return DiagnosticExplanationRequest(
            intent=plan.intent,
            findings=tuple(
                DiagnosticExplanationFinding(
                    code=finding.code,
                    category=finding.category,
                    severity=finding.severity,
                    title=finding.title,
                    evidence_fields=tuple(sorted(finding.evidence)),
                )
                for finding in report.findings[:20]
            ),
        )

    def request_consent(
        self, plan: DiagnosticPlan, report: DiagnosticReport
    ) -> ExternalDataConsentRequest:
        """Request exact approval for qualitative finding metadata only."""
        request = self.build_request(plan, report)
        return self._consent.request(
            purpose=ExternalDataPurpose.EXPLANATION,
            provider=self._provider.name,
            payload=request.model_dump(mode="json"),
            object_summary=(
                "Send finding codes, categories, severity, titles, and evidence field names. "
                "No values, paths, process names, commands, services, or software list are sent."
            ),
        )

    async def explain(
        self,
        plan: DiagnosticPlan,
        report: DiagnosticReport,
        confirmation_id: UUID,
    ) -> DiagnosticNarrativeDraft:
        """Return only qualitative observations bound to existing finding codes."""
        request = self.build_request(plan, report)
        payload = request.model_dump(mode="json")
        self._consent.require_approved(
            confirmation_id,
            purpose=ExternalDataPurpose.EXPLANATION,
            provider=self._provider.name,
            payload=payload,
        )
        result = await self._provider.explain_system_diagnostics(request)
        allowed_codes = {finding.code for finding in report.findings}
        if any(item.finding_code not in allowed_codes for item in result.narrative.observations):
            raise ValueError("Provider explanation referenced an unknown finding")
        return result.narrative
