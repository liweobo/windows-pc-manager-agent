from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import BaseModel

from pc_manager_agent.browser.fake import FakeBrowserAdapter
from pc_manager_agent.domain.browser import BrowserActionKind, BrowserActionRequest
from pc_manager_agent.tools.browser_tools import (
    BrowserActionInput,
    BrowserDocumentDownloadTool,
    BrowserDownloadInput,
    BrowserElementActionTool,
    BrowserNavigateInput,
    BrowserNavigateTool,
    BrowserObserveInput,
    BrowserObserveTool,
    BrowserSessionOpenInput,
    BrowserSessionOpenTool,
)
from pc_manager_agent.tools.manifest import CancellationToken


class _WrongInput(BaseModel):
    value: str = "wrong"


def _cancelled() -> CancellationToken:
    token = CancellationToken()
    token.cancel()
    return token


@pytest.mark.parametrize(
    ("tool", "input_model"),
    [
        (BrowserSessionOpenTool(FakeBrowserAdapter()), BrowserSessionOpenInput()),
        (
            BrowserNavigateTool(FakeBrowserAdapter()),
            BrowserNavigateInput(url="https://example.com/"),
        ),
        (BrowserObserveTool(FakeBrowserAdapter()), BrowserObserveInput(session_id=str(uuid4()))),
    ],
)
def test_r0_tools_reject_wrong_types_and_pre_cancelled_work(tool, input_model) -> None:
    assert tool.manifest.requires_confirmation is True
    with pytest.raises(TypeError, match="unexpected input"):
        tool.execute(_WrongInput(), CancellationToken())
    with pytest.raises(RuntimeError, match="CANCELLED"):
        tool.execute(input_model, _cancelled())


def test_all_five_tools_delegate_only_typed_requests(tmp_path: Path) -> None:
    adapter = FakeBrowserAdapter()
    token = CancellationToken()
    open_tool = BrowserSessionOpenTool(adapter)
    session = open_tool.execute(BrowserSessionOpenInput(headless=True), token)
    navigate_tool = BrowserNavigateTool(adapter)
    observation = navigate_tool.execute(BrowserNavigateInput(url="https://example.com/"), token)
    assert observation.session_id == session.session_id  # type: ignore[attr-defined]

    observe_tool = BrowserObserveTool(adapter)
    observed = observe_tool.execute(
        BrowserObserveInput(session_id=str(session.session_id)),
        token,  # type: ignore[attr-defined]
    )
    element = observed.elements[0]  # type: ignore[attr-defined]
    action = BrowserActionRequest(
        session_id=element.session_id,
        page_id=element.page_id,
        navigation_id=element.navigation_id,
        kind=BrowserActionKind.EXPAND,
        element=element,
    )
    action_tool = BrowserElementActionTool(adapter)
    assert action_tool.manifest.name == "browser.element.activate"
    assert action_tool.execute(BrowserActionInput(action=action), token).completed is True  # type: ignore[attr-defined]

    download_action = action.model_copy(update={"kind": BrowserActionKind.DOWNLOAD_DOCUMENT})
    download_tool = BrowserDocumentDownloadTool(adapter)
    assert download_tool.manifest.name == "browser.document.download"
    staged = download_tool.execute(
        BrowserDownloadInput(action=download_action, temporary_directory=tmp_path), token
    )
    assert staged.temporary_path.exists()  # type: ignore[attr-defined]


def test_observe_detects_session_mismatch_and_write_tools_check_guards(tmp_path: Path) -> None:
    adapter = FakeBrowserAdapter()
    adapter.start()
    observe_tool = BrowserObserveTool(adapter)
    with pytest.raises(RuntimeError, match="SESSION_MISMATCH"):
        observe_tool.execute(BrowserObserveInput(session_id=str(uuid4())), CancellationToken())

    observation = adapter.observe()
    action = BrowserActionRequest(
        session_id=observation.session_id,
        page_id=observation.page_id,
        navigation_id=observation.navigation_id,
        kind=BrowserActionKind.EXPAND,
        element=observation.elements[0],
    )
    for tool, request in (
        (BrowserElementActionTool(adapter), BrowserActionInput(action=action)),
        (
            BrowserDocumentDownloadTool(adapter),
            BrowserDownloadInput(
                action=action.model_copy(update={"kind": BrowserActionKind.DOWNLOAD_DOCUMENT}),
                temporary_directory=tmp_path,
            ),
        ),
    ):
        with pytest.raises(TypeError, match="unexpected input"):
            tool.execute(_WrongInput(), CancellationToken())
        with pytest.raises(RuntimeError, match="CANCELLED"):
            tool.execute(request, _cancelled())
