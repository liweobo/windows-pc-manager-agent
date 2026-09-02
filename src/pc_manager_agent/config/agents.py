"""Hard bounds for the Stage 5D coordination and context layer."""

from pydantic import Field, model_validator

from pc_manager_agent.domain.plans import FrozenModel


class AgentRuntimeLimits(FrozenModel):
    """Conservative limits that model output cannot relax."""

    max_task_nodes: int = Field(default=32, ge=1, le=64)
    max_graph_depth: int = Field(default=8, ge=1, le=12)
    max_agent_delegations: int = Field(default=12, ge=0, le=32)
    max_delegation_depth: int = Field(default=3, ge=0, le=5)
    max_model_calls: int = Field(default=12, ge=0, le=32)
    max_model_retries_per_node: int = Field(default=1, ge=0, le=1)
    max_messages: int = Field(default=8, ge=1, le=20)
    max_context_chars: int = Field(default=12_000, ge=256, le=32_000)
    max_document_chunks: int = Field(default=4, ge=0, le=8)
    max_web_chunks: int = Field(default=4, ge=0, le=8)
    max_memory_entries: int = Field(default=8, ge=0, le=20)
    max_structured_references: int = Field(default=32, ge=1, le=100)
    delegation_ttl_seconds: int = Field(default=120, ge=15, le=300)
    recent_reference_ttl_seconds: int = Field(default=600, ge=30, le=3_600)

    @model_validator(mode="after")
    def require_consistent_limits(self) -> "AgentRuntimeLimits":
        """Keep delegation and context subsets no larger than their parent budgets."""
        if self.max_delegation_depth > self.max_graph_depth:
            raise ValueError("Delegation depth cannot exceed graph depth")
        if self.max_document_chunks + self.max_web_chunks > self.max_messages:
            raise ValueError("Untrusted chunks cannot exceed the message budget")
        return self
