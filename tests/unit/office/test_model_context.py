from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from openai import APIError
from tests.integration.office.test_edit_flow import Harness
from tests.integration.office.test_edit_flow import harness as harness

from pc_manager_agent.domain.office_documents import OfficeError
from pc_manager_agent.office.context import (
    DocumentContextBuilder,
    OfficeModelProposal,
    OfficeModelResult,
    OfficeQuote,
)
from pc_manager_agent.orchestration.office_model import OfficeModelService
from pc_manager_agent.providers.llm.openai_office import OpenAIOfficeProvider


class FakeProvider:
    destination = "Test provider | no-network | fixed model"
    calls = 0

    async def propose(self, request):
        self.calls += 1
        chunk = request.chunks[0]
        return OfficeModelResult(
            proposal=OfficeModelProposal(
                quotes=(OfficeQuote(chunk_id=chunk.chunk_id, quote=chunk.text[:10]),)
            ),
            provider="fake",
            request_id="synthetic-id",
        )


def prepare(harness, tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("Actual synthetic content only. " * 100, encoding="utf-8")
    result = harness.read(source)
    provider = FakeProvider()
    service = OfficeModelService(
        harness.reads, provider, harness.edits._confirmations, harness.edits._audit
    )
    chunk = DocumentContextBuilder(harness.reads.limits).chunks(result)[0]
    disclosure = service.prepare(
        (result.reference.document_id,), (chunk.chunk_id,), "Quote one useful point"
    )
    return source, service, provider, disclosure


def test_external_consent_is_separate_single_use_and_content_free_audit(
    harness: Harness, tmp_path: Path
):
    source, service, provider, disclosure = prepare(harness, tmp_path)
    assert provider.calls == 0
    assert str(source) not in disclosure.request.model_dump_json()
    proposal = asyncio.run(service.confirm_and_propose(disclosure.disclosure_id, True))
    assert proposal.quotes
    assert provider.calls == 1
    with pytest.raises(OfficeError, match="DISCLOSURE_MISSING"):
        asyncio.run(service.confirm_and_propose(disclosure.disclosure_id, True))
    assert b"Actual synthetic" not in (tmp_path / "audit.db").read_bytes()


@pytest.mark.parametrize(
    "failure", ["reject", "changed_source", "changed_provider", "provider_error", "invalid_quote"]
)
def test_model_fail_closed(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure
):
    source, service, provider, disclosure = prepare(harness, tmp_path)
    if failure == "reject":
        assert asyncio.run(service.confirm_and_propose(disclosure.disclosure_id, False)) is None
        assert provider.calls == 0
        return
    if failure == "changed_source":
        source.write_bytes(b"changed after disclosure")
    elif failure == "changed_provider":
        provider.destination = "different service"
    else:

        async def bad(_):
            if failure == "provider_error":
                raise RuntimeError("Sensitive exception must not enter audit")
            return OfficeModelResult(
                provider="fake",
                proposal=OfficeModelProposal(
                    quotes=(OfficeQuote(chunk_id="wrong", quote="fabricated"),)
                ),
            )

        monkeypatch.setattr(provider, "propose", bad)
    with pytest.raises(OfficeError):
        asyncio.run(service.confirm_and_propose(disclosure.disclosure_id, True))


def test_openai_adapter_fixed_endpoint_no_tools_no_retry_and_trace(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _, _, _, disclosure = prepare(harness, tmp_path)
    calls = []
    chunk = disclosure.request.chunks[0]

    class FakeClient:
        closed = False

        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.responses = self

        async def parse(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                status="completed",
                output_parsed=OfficeModelProposal(
                    quotes=(OfficeQuote(chunk_id=chunk.chunk_id, quote=chunk.text[:5]),)
                ),
                _request_id="req_synthetic",
            )

        async def close(self):
            self.closed = True

    monkeypatch.setattr("pc_manager_agent.providers.llm.openai_office.AsyncOpenAI", FakeClient)
    provider = OpenAIOfficeProvider("configured-model", "synthetic-test-value")
    response = asyncio.run(provider.propose(disclosure.request))
    assert response.request_id == "req_synthetic"
    assert calls[0]["base_url"] == "https://api.openai.com/v1"
    assert calls[0]["max_retries"] == 0
    assert calls[1]["store"] is False
    assert "tools" not in calls[1]
    assert "UNTRUSTED DATA" in calls[1]["instructions"]
    assert "api.openai.com" in provider.destination
    for model, key in (("", "fake"), ("test", "")):
        with pytest.raises(OfficeError):
            OpenAIOfficeProvider(model, key)


@pytest.mark.parametrize("failure", ["incomplete", "refusal", "api"])
def test_openai_errors_are_safe(harness: Harness, tmp_path: Path, failure):
    _, _, _, disclosure = prepare(harness, tmp_path)

    class Responses:
        async def parse(self, **kwargs):
            if failure == "api":
                raise APIError(
                    "secret raw error",
                    request=httpx.Request("POST", "https://invalid.example"),
                    body=None,
                )
            return SimpleNamespace(
                status="incomplete" if failure == "incomplete" else "completed", output_parsed=None
            )

    provider = OpenAIOfficeProvider(
        "test", "synthetic", client=SimpleNamespace(responses=Responses())
    )
    with pytest.raises(OfficeError) as raised:
        asyncio.run(provider.propose(disclosure.request))
    assert "secret" not in str(raised.value)


def test_context_known_scope_expansion_and_nonquoted_claims_fail(harness: Harness, tmp_path: Path):
    from pc_manager_agent.domain.office_plans import OfficeIntentDraft, OfficeTaskIntent
    from pc_manager_agent.office.context import validate_proposal

    _, _, _, disclosure = prepare(harness, tmp_path)
    for intent in (
        OfficeIntentDraft(intent=OfficeTaskIntent.EDIT_DOCUMENT, document_ids=(uuid4(),)),
        OfficeIntentDraft(
            intent=OfficeTaskIntent.EDIT_DOCUMENT, document_ids=(), source_references=("invented",)
        ),
    ):
        with pytest.raises(OfficeError):
            validate_proposal(disclosure.request, OfficeModelProposal(intent=intent))
