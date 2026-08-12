"""Provider-neutral contract for structured task planning."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pc_manager_agent.domain.file_analysis import (
    AnalysisType,
    FileAnalysisFilters,
    FileAnalysisIntentDraft,
    FileAnalysisSummary,
)
from pc_manager_agent.domain.file_operations import (
    FileOperationIntentDraft,
    OperationType,
    OrganizationGroup,
    RenameRuleType,
)
from pc_manager_agent.domain.plans import TaskPlan
from pc_manager_agent.domain.system_diagnostics import (
    DiagnosticCategory,
    DiagnosticIntent,
    DiagnosticIntentDraft,
    FindingSeverity,
    SystemCollector,
)


class PlannerRequest(BaseModel):
    """Explicit data that a caller proposes to send to an LLM provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_goal: str = Field(min_length=1, max_length=2_000)
    included_paths: tuple[Path, ...]
    excluded_paths: tuple[Path, ...] = ()
    allowed_tools: tuple[str, ...]


class ProviderPlanResult(BaseModel):
    """Validated provider result plus trace metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan: TaskPlan
    provider: str
    request_id: str | None = None


class AuthorizedRootOption(BaseModel):
    """Opaque local root identifier and non-sensitive label offered to a model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    root_id: str
    label: str = Field(min_length=1, max_length=120)


class FileAnalysisPlannerRequest(BaseModel):
    """Explicit minimal data proposed for structured file-analysis planning."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_goal: str = Field(min_length=1, max_length=2_000)
    authorized_roots: tuple[AuthorizedRootOption, ...]
    allowed_analyses: tuple[AnalysisType, ...]
    allowed_tools: tuple[str, ...]


class ProviderIntentResult(BaseModel):
    """Validated untrusted intent draft plus provider trace metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: FileAnalysisIntentDraft
    provider: str
    request_id: str | None = None


class FileOperationPlannerRequest(BaseModel):
    """Minimal root-ID-only data proposed for Stage 2A intent planning."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_goal: str = Field(min_length=1, max_length=2_000)
    authorized_roots: tuple[AuthorizedRootOption, ...]
    allowed_operations: tuple[OperationType, ...]
    allowed_rename_rules: tuple[RenameRuleType, ...]
    allowed_grouping: tuple[OrganizationGroup, ...]
    allowed_tools: tuple[str, ...]


class ProviderFileOperationIntentResult(BaseModel):
    """Validated but untrusted Stage 2A intent plus provider trace metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: FileOperationIntentDraft
    provider: str
    request_id: str | None = None


class AnalysisNarrativeDraft(BaseModel):
    """Qualitative provider observations; deterministic code owns every number."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observations: tuple[str, ...] = Field(min_length=1, max_length=5)

    @field_validator("observations")
    @classmethod
    def reject_numeric_claims(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject narratives that could contradict measured aggregate values."""
        if any(any(character.isdigit() for character in item) for item in value):
            raise ValueError("Provider narrative must not contain numeric claims")
        return value


class AnalysisExplanationRequest(BaseModel):
    """Aggregate-only result data proposed for optional external explanation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: FileAnalysisSummary
    filters: FileAnalysisFilters
    analyses: tuple[AnalysisType, ...]


class ProviderNarrativeResult(BaseModel):
    """Validated qualitative narrative plus provider trace metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    narrative: AnalysisNarrativeDraft
    provider: str
    request_id: str | None = None


class DiagnosticPlannerRequest(BaseModel):
    """Minimal Stage 3 planner payload with no local system snapshot or path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_goal: str = Field(min_length=1, max_length=2_000)
    allowed_intents: tuple[DiagnosticIntent, ...]
    allowed_collectors: tuple[SystemCollector, ...]


class ProviderDiagnosticIntentResult(BaseModel):
    """Validated but untrusted diagnostic intent with provider trace metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: DiagnosticIntentDraft
    provider: str
    request_id: str | None = None


class DiagnosticExplanationFinding(BaseModel):
    """Path-free and process-free deterministic finding offered for explanation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=100)
    category: DiagnosticCategory
    severity: FindingSeverity
    title: str = Field(min_length=1, max_length=300)
    evidence_fields: tuple[str, ...]


class DiagnosticExplanationRequest(BaseModel):
    """Minimal finding metadata proposed for optional external explanation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: DiagnosticIntent
    findings: tuple[DiagnosticExplanationFinding, ...] = Field(max_length=20)


class DiagnosticNarrativeObservation(BaseModel):
    """Qualitative provider explanation bound to one deterministic finding code."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_code: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=1_000)

    @field_validator("text")
    @classmethod
    def reject_numeric_claims(cls, value: str) -> str:
        """Prevent the provider from inventing or restating measured numbers."""
        if any(character.isdigit() for character in value):
            raise ValueError("Provider diagnostic explanation must not contain numeric claims")
        return value


class DiagnosticNarrativeDraft(BaseModel):
    """Bounded qualitative Stage 3 explanation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observations: tuple[DiagnosticNarrativeObservation, ...] = Field(max_length=20)


class ProviderDiagnosticNarrativeResult(BaseModel):
    """Validated diagnostic narrative with provider trace metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    narrative: DiagnosticNarrativeDraft
    provider: str
    request_id: str | None = None


class LLMProvider(ABC):
    """Replaceable structured planner interface."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return a stable provider identifier."""
        raise NotImplementedError

    @abstractmethod
    async def create_plan(self, request: PlannerRequest) -> ProviderPlanResult:
        """Return a schema-validated plan without executing it."""
        raise NotImplementedError

    async def create_file_analysis_intent(
        self,
        request: FileAnalysisPlannerRequest,
    ) -> ProviderIntentResult:
        """Return an untrusted intent draft that still needs deterministic compilation."""
        raise NotImplementedError

    async def create_file_operation_intent(
        self,
        request: FileOperationPlannerRequest,
    ) -> ProviderFileOperationIntentResult:
        """Return an untrusted finite Stage 2A intent that still needs local compilation."""
        raise NotImplementedError

    async def explain_file_analysis(
        self,
        request: AnalysisExplanationRequest,
    ) -> ProviderNarrativeResult:
        """Return qualitative observations about aggregate-only measured results."""
        raise NotImplementedError

    async def create_diagnostic_intent(
        self,
        request: DiagnosticPlannerRequest,
    ) -> ProviderDiagnosticIntentResult:
        """Return a finite untrusted Stage 3 intent that needs local compilation."""
        raise NotImplementedError

    async def explain_system_diagnostics(
        self,
        request: DiagnosticExplanationRequest,
    ) -> ProviderDiagnosticNarrativeResult:
        """Return qualitative path-free explanations bound to local finding codes."""
        raise NotImplementedError
