"""Hard limits for the Stage 5E final coordination layer."""

from pydantic import Field, model_validator

from pc_manager_agent.domain.plans import FrozenModel


class FinalTaskLimits(FrozenModel):
    """Conservative orchestration limits that tasks and models cannot relax."""

    max_task_nodes: int = Field(default=32, ge=1, le=64)
    max_agent_calls: int = Field(default=12, ge=0, le=32)
    max_tool_preparations: int = Field(default=32, ge=1, le=64)
    max_browser_navigations: int = Field(default=20, ge=0, le=50)
    max_files_scanned: int = Field(default=50_000, ge=1, le=100_000)
    max_runtime_seconds: int = Field(default=3_600, ge=60, le=21_600)
    max_llm_calls: int = Field(default=12, ge=0, le=32)
    max_delegation_depth: int = Field(default=3, ge=0, le=5)
    max_concurrent_reads: int = Field(default=3, ge=1, le=8)
    max_background_workers: int = Field(default=4, ge=1, le=8)
    max_safe_read_retries: int = Field(default=1, ge=0, le=2)
    plan_confirmation_ttl_seconds: int = Field(default=300, ge=30, le=900)

    @model_validator(mode="after")
    def require_consistent_worker_limits(self) -> "FinalTaskLimits":
        """Keep parallel read work inside the total background-worker budget."""
        if self.max_concurrent_reads > self.max_background_workers:
            raise ValueError("Concurrent reads cannot exceed background workers")
        return self
