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
from pc_manager_agent.domain.plans import TaskPlan


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

    async def explain_file_analysis(
        self,
        request: AnalysisExplanationRequest,
    ) -> ProviderNarrativeResult:
        """Return qualitative observations about aggregate-only measured results."""
        raise NotImplementedError
