from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from openai import APIConnectionError

from pc_manager_agent.app.runtime import ApplicationRuntime, ProviderConfigurationError
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.providers.llm.base import PlannerRequest
from pc_manager_agent.providers.llm.openai_provider import OpenAILLMProvider, OpenAIProviderError
from tests.unit.test_models import build_plan


class FakeResponse:
    def __init__(self, output: object) -> None:
        self.output_parsed = output
        self._request_id = "request-123"


class FakeResponses:
    def __init__(self, output: object, error: Exception | None = None) -> None:
        self.output = output
        self.error = error
        self.arguments: dict[str, object] = {}

    async def parse(self, **kwargs: object) -> object:
        self.arguments = kwargs
        if self.error:
            raise self.error
        return FakeResponse(self.output)


class FakeClient:
    def __init__(self, responses: FakeResponses) -> None:
        self.responses = responses


def planner_request(root: Path) -> PlannerRequest:
    return PlannerRequest(
        user_goal="scan",
        included_paths=(root,),
        allowed_tools=("file.scan",),
    )


def test_openai_provider_returns_validated_plan(tmp_path: Path) -> None:
    responses = FakeResponses(build_plan(tmp_path))
    provider = OpenAILLMProvider(
        model="test-model", api_key="test-key", client=FakeClient(responses)
    )
    result = asyncio.run(provider.create_plan(planner_request(tmp_path)))
    assert result.plan.summary == "safe scan"
    assert result.provider == "openai"
    assert result.request_id == "request-123"
    assert responses.arguments["model"] == "test-model"
    assert "test-key" not in str(responses.arguments)


def test_openai_provider_rejects_malformed_and_api_errors(tmp_path: Path) -> None:
    malformed = OpenAILLMProvider(
        model="test-model",
        api_key="test-key",
        client=FakeClient(FakeResponses({"not": "a plan"})),
    )
    with pytest.raises(OpenAIProviderError, match="invalid"):
        asyncio.run(malformed.create_plan(planner_request(tmp_path)))
    error = APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    failing = OpenAILLMProvider(
        model="test-model",
        api_key="test-key",
        client=FakeClient(FakeResponses(None, error)),
    )
    with pytest.raises(OpenAIProviderError, match="failed"):
        asyncio.run(failing.create_plan(planner_request(tmp_path)))


def test_openai_provider_requires_explicit_configuration() -> None:
    with pytest.raises(ValueError, match="model"):
        OpenAILLMProvider(model=" ", api_key="key")
    with pytest.raises(ValueError, match="API_KEY"):
        OpenAILLMProvider(model="model", api_key=" ")


def test_runtime_provider_factory(tmp_path: Path) -> None:
    disabled = ApplicationRuntime(AppSettings(data_directory=tmp_path / "disabled"))
    assert disabled.create_llm_provider() is None
    missing = ApplicationRuntime(
        AppSettings(
            data_directory=tmp_path / "missing",
            llm_provider="openai",
            openai_model="model",
        )
    )
    with pytest.raises(ProviderConfigurationError):
        missing.create_llm_provider()
    configured = ApplicationRuntime(
        AppSettings(
            data_directory=tmp_path / "configured",
            llm_provider="openai",
            openai_model="model",
            openai_api_key="test-key",
        )
    )
    assert isinstance(configured.create_llm_provider(), OpenAILLMProvider)
    disabled.close()
    missing.close()
    configured.close()
