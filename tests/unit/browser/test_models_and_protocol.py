from uuid import uuid4

import pytest
from pydantic import ValidationError

from pc_manager_agent.browser.protocol import BrowserWorkerCommand, BrowserWorkerRequest
from pc_manager_agent.domain.browser import (
    BrowserActionKind,
    BrowserActionRequest,
    BrowserElementReference,
    BrowserElementRole,
    BrowserObservation,
)


def _reference() -> BrowserElementReference:
    return BrowserElementReference.create(
        session_id=uuid4(),
        page_id=uuid4(),
        navigation_id=uuid4(),
        role=BrowserElementRole.LINK,
        accessible_name="Documentation",
        href="https://example.com/docs",
    )


def test_element_fingerprint_rejects_tampering() -> None:
    reference = _reference()
    with pytest.raises(ValidationError, match="fingerprint"):
        BrowserElementReference.model_validate(
            {**reference.model_dump(mode="json"), "accessible_name": "Buy now"}
        )


def test_observation_rejects_foreign_generation() -> None:
    reference = _reference()
    with pytest.raises(ValidationError, match="another page generation"):
        BrowserObservation(
            session_id=reference.session_id,
            page_id=reference.page_id,
            navigation_id=uuid4(),
            url="https://example.com/",
            elements=(reference,),
        )


def test_action_shape_has_no_generic_arguments() -> None:
    reference = _reference()
    action = BrowserActionRequest(
        session_id=reference.session_id,
        page_id=reference.page_id,
        navigation_id=reference.navigation_id,
        kind=BrowserActionKind.OPEN_LINK,
        element=reference,
    )
    with pytest.raises(ValidationError):
        BrowserActionRequest.model_validate(
            {**action.model_dump(mode="json"), "selector": "#danger"}
        )
    with pytest.raises(ValidationError, match="Only search and filter"):
        BrowserActionRequest(
            session_id=reference.session_id,
            page_id=reference.page_id,
            navigation_id=reference.navigation_id,
            kind=BrowserActionKind.OPEN_LINK,
            element=reference,
            text="hidden argument",
        )


def test_worker_protocol_rejects_command_shaped_payload_confusion() -> None:
    with pytest.raises(ValidationError):
        BrowserWorkerRequest(command=BrowserWorkerCommand.OBSERVE, url="https://example.com/")
    with pytest.raises(ValidationError):
        BrowserWorkerRequest.model_validate(
            {"command": "START", "headless": True, "shell": "powershell"}
        )
    request = BrowserWorkerRequest(
        command=BrowserWorkerCommand.NAVIGATE,
        url="http://example.com/",
        allow_insecure_http=True,
    )
    assert request.allow_insecure_http is True
