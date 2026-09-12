from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from openai import APIConnectionError

from pc_manager_agent.app.runtime import ApplicationRuntime, ProviderConfigurationError
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.domain.file_analysis import (
    AnalysisType,
    FileAnalysisFilters,
    FileAnalysisIntentDraft,
    FileAnalysisSummary,
)
from pc_manager_agent.domain.file_operations import (
    FileOperationIntentDraft,
    FileSelectionRule,
    OperationType,
    OrganizationGroup,
    RenameRuleType,
)
from pc_manager_agent.domain.reports import ScanStatus
from pc_manager_agent.providers.llm.base import (
    AnalysisExplanationRequest,
    AnalysisNarrativeDraft,
    AuthorizedRootOption,
    FileAnalysisPlannerRequest,
    FileOperationPlannerRequest,
    PlannerRequest,
)
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


def test_openai_provider_parses_file_intent_and_qualitative_narrative() -> None:
    intent = FileAnalysisIntentDraft(
        authorized_root_ids=("00000000-0000-0000-0000-000000000001",),
        analyses=(AnalysisType.LARGE_FILES,),
    )
    intent_responses = FakeResponses(intent)
    provider = OpenAILLMProvider(
        model="test-model",
        api_key="test-key",
        client=FakeClient(intent_responses),
    )
    intent_result = asyncio.run(
        provider.create_file_analysis_intent(
            FileAnalysisPlannerRequest(
                user_goal="find large files",
                authorized_roots=(AuthorizedRootOption(root_id="opaque", label="Downloads"),),
                allowed_analyses=(AnalysisType.LARGE_FILES,),
                allowed_tools=("file.scan", "file.analyze.large"),
            )
        )
    )
    assert intent_result.intent.analyses == (AnalysisType.LARGE_FILES,)

    narrative = AnalysisNarrativeDraft(observations=("Video files are the main category",))
    provider = OpenAILLMProvider(
        model="test-model",
        api_key="test-key",
        client=FakeClient(FakeResponses(narrative)),
    )
    narrative_result = asyncio.run(
        provider.explain_file_analysis(
            AnalysisExplanationRequest(
                summary=FileAnalysisSummary(
                    files_scanned=2,
                    directories_scanned=1,
                    total_bytes=10,
                    matching_files=1,
                    matching_bytes=8,
                    errors=0,
                    status=ScanStatus.COMPLETED,
                ),
                filters=FileAnalysisFilters(),
                analyses=(AnalysisType.LARGE_FILES,),
            )
        )
    )
    assert narrative_result.narrative == narrative


def test_provider_narrative_rejects_invented_numeric_claims() -> None:
    with pytest.raises(ValueError, match="numeric"):
        AnalysisNarrativeDraft(observations=("There are 99 matching files",))


def test_openai_provider_parses_file_operation_intent() -> None:
    root_id = "00000000-0000-0000-0000-000000000001"
    intent = FileOperationIntentDraft(
        selection=FileSelectionRule(root_ids=(root_id,), extensions=(".pdf",)),
        destination_root_id=root_id,
        destination_subdirectory=("PDF",),
        group_by=OrganizationGroup.MODIFIED_YEAR,
        requested_operation=OperationType.MOVE_FILE,
    )
    responses = FakeResponses(intent)
    provider = OpenAILLMProvider(
        model="test-model",
        api_key="test-key",
        client=FakeClient(responses),
    )
    result = asyncio.run(
        provider.create_file_operation_intent(
            FileOperationPlannerRequest(
                user_goal="organize PDFs",
                authorized_roots=(AuthorizedRootOption(root_id=root_id, label="Downloads"),),
                allowed_operations=tuple(OperationType),
                allowed_rename_rules=tuple(RenameRuleType),
                allowed_grouping=tuple(OrganizationGroup),
                allowed_tools=("file.mkdir", "file.move", "file.rename"),
            )
        )
    )
    assert result.intent.group_by is OrganizationGroup.MODIFIED_YEAR
    assert responses.arguments["model"] == "test-model"


def test_openai_provider_uses_official_openai_destination() -> None:
    """Verify OpenAILLMProvider connects only to official OpenAI endpoint."""
    with patch("pc_manager_agent.providers.llm.openai_provider.AsyncOpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_openai.return_value = mock_client

        OpenAILLMProvider(model="gpt-4", api_key="test-key-12345")

        mock_openai.assert_called_once()
        call_kwargs = mock_openai.call_args[1]
        assert call_kwargs["api_key"] == "test-key-12345"
        assert call_kwargs["base_url"] == "https://api.openai.com/v1"
        assert call_kwargs["timeout"] == 30.0
        assert call_kwargs["max_retries"] == 1


def test_openai_provider_never_routes_to_agentrouter() -> None:
    """Regression test: ensure OPENAI_API_KEY never goes to third-party gateway."""
    with patch("pc_manager_agent.providers.llm.openai_provider.AsyncOpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_openai.return_value = mock_client

        OpenAILLMProvider(model="gpt-4", api_key="sk-test")

        call_kwargs = mock_openai.call_args[1]
        base_url = call_kwargs["base_url"]
        assert "agentrouter" not in base_url.lower(), (
            f"OpenAI provider must not route to agentrouter; got base_url={base_url}"
        )
        assert "api.openai.com" in base_url


def test_openai_provider_exposes_destination_metadata() -> None:
    """Verify provider declares its network destination for audit and disclosure."""
    responses = FakeResponses(None)
    provider = OpenAILLMProvider(
        model="gpt-4o",
        api_key="test-key",
        client=FakeClient(responses),
    )

    destination = provider.destination
    assert "OpenAI" in destination
    assert "https://api.openai.com/v1" in destination
    assert "gpt-4o" in destination
    assert "test-key" not in destination


def test_llm_provider_defaults_to_disabled(tmp_path: Path) -> None:
    """Verify runtime safe default keeps provider disabled without explicit config."""
    settings = AppSettings(data_directory=tmp_path)
    assert settings.llm_provider == "disabled"

    runtime = ApplicationRuntime(settings)
    provider = runtime.create_llm_provider()
    assert provider is None
    runtime.close()


def test_provider_factory_returns_none_when_disabled(tmp_path: Path) -> None:
    """Verify explicitly setting disabled prevents provider instantiation."""
    settings = AppSettings(
        data_directory=tmp_path,
        llm_provider="disabled",
        openai_model="gpt-4",
        openai_api_key="present-but-ignored",
    )
    runtime = ApplicationRuntime(settings)
    provider = runtime.create_llm_provider()
    assert provider is None
    runtime.close()


def test_provider_factory_builds_openai_only_when_explicitly_enabled(tmp_path: Path) -> None:
    """Verify provider is only built when explicitly enabled, not by accident."""
    runtime = ApplicationRuntime(
        AppSettings(
            data_directory=tmp_path,
            llm_provider="openai",
            openai_model="gpt-4",
            openai_api_key="sk-test",
        )
    )
    provider = runtime.create_llm_provider()
    assert isinstance(provider, OpenAILLMProvider)
    assert provider.name == "openai"
    runtime.close()


def test_openai_provider_requires_explicit_api_key() -> None:
    """Verify OpenAI provider fails safely without credential."""
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAILLMProvider(model="gpt-4", api_key="")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAILLMProvider(model="gpt-4", api_key="   ")


def test_openai_provider_requires_explicit_model() -> None:
    """Verify OpenAI provider fails safely without model specification."""
    with pytest.raises(ValueError, match="model"):
        OpenAILLMProvider(model="", api_key="sk-test")
    with pytest.raises(ValueError, match="model"):
        OpenAILLMProvider(model="   ", api_key="sk-test")
