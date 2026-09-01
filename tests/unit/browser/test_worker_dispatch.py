from __future__ import annotations

import io
from pathlib import Path
from uuid import UUID

from pc_manager_agent.browser import worker
from pc_manager_agent.browser.fake import FakeBrowserAdapter
from pc_manager_agent.browser.protocol import (
    BrowserWorkerCommand,
    BrowserWorkerPayloadType,
    BrowserWorkerRequest,
    BrowserWorkerResponse,
)
from pc_manager_agent.domain.browser import BrowserActionKind, BrowserActionRequest


def test_dispatch_covers_the_complete_finite_worker_vocabulary(tmp_path: Path) -> None:
    adapter = FakeBrowserAdapter()
    started = worker._dispatch(
        adapter, BrowserWorkerRequest(command=BrowserWorkerCommand.START, headless=True)
    )
    assert started.payload_type is BrowserWorkerPayloadType.SESSION

    navigated = worker._dispatch(
        adapter,
        BrowserWorkerRequest(
            command=BrowserWorkerCommand.NAVIGATE,
            url="https://example.com/",
        ),
    )
    assert navigated.payload_type is BrowserWorkerPayloadType.OBSERVATION
    observed = worker._dispatch(adapter, BrowserWorkerRequest(command=BrowserWorkerCommand.OBSERVE))
    observation = observed.payload_json
    assert observation is not None

    from pc_manager_agent.domain.browser import BrowserObservation

    model = BrowserObservation.model_validate_json(observation)
    performed = worker._dispatch(
        adapter,
        BrowserWorkerRequest(
            command=BrowserWorkerCommand.PERFORM,
            action_json=BrowserActionRequest(
                session_id=model.session_id,
                page_id=model.page_id,
                navigation_id=model.navigation_id,
                kind=BrowserActionKind.OBSERVE,
            ).model_dump_json(),
        ),
    )
    assert performed.payload_type is BrowserWorkerPayloadType.ACTION_RESULT

    downloaded = worker._dispatch(
        adapter,
        BrowserWorkerRequest(
            command=BrowserWorkerCommand.DOWNLOAD,
            action_json=BrowserActionRequest(
                session_id=model.session_id,
                page_id=model.page_id,
                navigation_id=model.navigation_id,
                kind=BrowserActionKind.DOWNLOAD_DOCUMENT,
                element=model.elements[0],
            ).model_dump_json(),
            temporary_directory=str(tmp_path),
        ),
    )
    assert downloaded.payload_type is BrowserWorkerPayloadType.DOWNLOAD
    assert (
        worker._dispatch(
            adapter, BrowserWorkerRequest(command=BrowserWorkerCommand.CANCEL)
        ).reason_code
        == "BROWSER_WORKER_CANCELLED"
    )

    close_adapter = FakeBrowserAdapter()
    close_adapter.start()
    assert (
        worker._dispatch(
            close_adapter, BrowserWorkerRequest(command=BrowserWorkerCommand.CLOSE)
        ).reason_code
        == "BROWSER_WORKER_CLOSED"
    )


def test_run_worker_does_not_reflect_malformed_input(monkeypatch) -> None:
    fake = FakeBrowserAdapter()
    input_stream = io.StringIO(
        "not-json\n"
        + BrowserWorkerRequest(command=BrowserWorkerCommand.START, headless=True).model_dump_json()
        + "\n"
        + BrowserWorkerRequest(command=BrowserWorkerCommand.CLOSE).model_dump_json()
        + "\n"
    )
    output_stream = io.StringIO()
    monkeypatch.setattr(worker, "PlaywrightBrowserAdapter", lambda: fake)
    monkeypatch.setattr(worker.sys, "stdin", input_stream)
    monkeypatch.setattr(worker.sys, "stdout", output_stream)

    assert worker.run_worker() == 0
    responses = [
        BrowserWorkerResponse.model_validate_json(line)
        for line in output_stream.getvalue().splitlines()
    ]
    assert responses[0].success is False
    assert responses[0].request_id == UUID(int=0)
    assert responses[-1].reason_code == "BROWSER_WORKER_CLOSED"


def test_request_id_helper_keeps_only_valid_correlation() -> None:
    request = BrowserWorkerRequest(command=BrowserWorkerCommand.OBSERVE)
    assert worker._request_id_or_zero(request.model_dump_json()) == request.request_id
    assert worker._request_id_or_zero("{}") == UUID(int=0)
