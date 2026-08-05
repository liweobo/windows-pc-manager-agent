"""Provider-neutral contract for structured task planning."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

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
