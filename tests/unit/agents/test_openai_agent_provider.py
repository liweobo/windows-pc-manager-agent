from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

from pc_manager_agent.domain.agents import AgentRole
from pc_manager_agent.domain.task_graph import TaskDomain, TaskNodeType
from pc_manager_agent.providers.llm.agent_base import (
    AgentGraphDraft,
    AgentGraphDraftNode,
    AgentPlanningRequest,
)
from pc_manager_agent.providers.llm.openai_agents import (
    OpenAIAgentLLMProvider,
    OpenAIAgentProviderError,
)


class _Responses:
    def __init__(self, output: object = None, error: Exception | None = None) -> None:
        self.output = output
        self.error = error
        self.arguments: dict[str, object] = {}

    async def parse(self, **kwargs: object) -> object:
        self.arguments = kwargs
        if self.error:
            raise self.error
        return SimpleNamespace(output_parsed=self.output, _request_id="request-stage5d")


def _request() -> AgentPlanningRequest:
    goal = "inspect system"
    return AgentPlanningRequest(
        task_id="11111111-1111-1111-1111-111111111111",
        user_goal=goal,
        goal_digest=hashlib.sha256(goal.encode()).hexdigest(),
        allowed_domains=(TaskDomain.SYSTEM,),
        allowed_roles=(AgentRole.PLANNER, AgentRole.SYSTEM),
        max_nodes=8,
        max_depth=4,
    )


def test_openai_agent_provider_returns_only_structured_draft() -> None:
    draft = AgentGraphDraft(
        nodes=(
            AgentGraphDraftNode(
                node_key="read",
                node_type=TaskNodeType.READ,
                domain=TaskDomain.SYSTEM,
                agent_role=AgentRole.SYSTEM,
            ),
        )
    )
    responses = _Responses(draft)
    provider = OpenAIAgentLLMProvider(
        model="test-model",
        api_key="synthetic-test-key",
        client=SimpleNamespace(responses=responses),
    )
    result = asyncio.run(provider.create_task_graph(_request()))
    assert result.draft == draft
    assert result.provider == "openai"
    assert result.request_id == "request-stage5d"
    assert responses.arguments["text_format"] is AgentGraphDraft
    assert "synthetic-test-key" not in str(responses.arguments)


def test_openai_agent_provider_sanitizes_invalid_and_api_failures() -> None:
    invalid = OpenAIAgentLLMProvider(
        model="test",
        api_key="synthetic",
        client=SimpleNamespace(responses=_Responses({"not": "a draft"})),
    )
    with pytest.raises(OpenAIAgentProviderError, match="invalid"):
        asyncio.run(invalid.create_task_graph(_request()))

    error = APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1"))
    failing = OpenAIAgentLLMProvider(
        model="test",
        api_key="synthetic",
        client=SimpleNamespace(responses=_Responses(error=error)),
    )
    with pytest.raises(OpenAIAgentProviderError, match="failed") as raised:
        asyncio.run(failing.create_task_graph(_request()))
    assert "api.openai.com" not in str(raised.value)


def test_agent_planning_configuration_and_goal_are_exact() -> None:
    with pytest.raises(ValueError, match="model"):
        OpenAIAgentLLMProvider(model=" ", api_key="synthetic")
    with pytest.raises(ValueError, match="API key"):
        OpenAIAgentLLMProvider(model="test", api_key=" ")
    with pytest.raises(ValueError, match="goal digest"):
        AgentPlanningRequest.model_validate(_request().model_dump() | {"goal_digest": "0" * 64})
