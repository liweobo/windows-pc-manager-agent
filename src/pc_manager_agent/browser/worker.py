"""Disposable JSON-lines Browser Worker entrypoint; it owns all Playwright objects."""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from pc_manager_agent.browser.playwright_adapter import PlaywrightBrowserAdapter
from pc_manager_agent.browser.protocol import (
    BrowserWorkerCommand,
    BrowserWorkerPayloadType,
    BrowserWorkerRequest,
    BrowserWorkerResponse,
)
from pc_manager_agent.domain.browser import (
    BrowserActionRequest,
    BrowserActionResult,
    BrowserObservation,
    BrowserSessionDescriptor,
)
from pc_manager_agent.domain.browser_downloads import BrowserWorkerDownload


def run_worker() -> int:
    """Process one command at a time; invalid input never becomes a browser call."""
    adapter = PlaywrightBrowserAdapter()
    try:
        for line in sys.stdin:
            should_close = False
            try:
                request = BrowserWorkerRequest.model_validate_json(line)
                response = _dispatch(adapter, request)
                should_close = request.command is BrowserWorkerCommand.CLOSE and response.success
            except Exception:
                # Do not reflect exception text: it can contain an untrusted URL or page data.
                response = BrowserWorkerResponse(
                    request_id=_request_id_or_zero(line),
                    success=False,
                    reason_code="BROWSER_WORKER_REQUEST_FAILED",
                )
            sys.stdout.write(response.model_dump_json() + "\n")
            sys.stdout.flush()
            if should_close:
                return 0
    finally:
        adapter.close()
    return 0


def _dispatch(
    adapter: PlaywrightBrowserAdapter,
    request: BrowserWorkerRequest,
) -> BrowserWorkerResponse:
    payload: (
        BrowserSessionDescriptor | BrowserObservation | BrowserActionResult | BrowserWorkerDownload
    )
    if request.command is BrowserWorkerCommand.START:
        payload = adapter.start(headless=bool(request.headless))
        payload_type = BrowserWorkerPayloadType.SESSION
    elif request.command is BrowserWorkerCommand.NAVIGATE:
        if request.url is None:
            raise ValueError("URL required")
        payload = adapter.navigate(request.url, allow_http=request.allow_insecure_http)
        payload_type = BrowserWorkerPayloadType.OBSERVATION
    elif request.command is BrowserWorkerCommand.OBSERVE:
        payload = adapter.observe()
        payload_type = BrowserWorkerPayloadType.OBSERVATION
    elif request.command is BrowserWorkerCommand.PERFORM:
        if request.action_json is None:
            raise ValueError("Action required")
        payload = adapter.perform(BrowserActionRequest.model_validate_json(request.action_json))
        payload_type = BrowserWorkerPayloadType.ACTION_RESULT
    elif request.command is BrowserWorkerCommand.DOWNLOAD:
        if request.action_json is None or request.temporary_directory is None:
            raise ValueError("Download fields required")
        action = BrowserActionRequest.model_validate_json(request.action_json)
        payload = adapter.download(action, Path(request.temporary_directory))
        payload_type = BrowserWorkerPayloadType.DOWNLOAD
    elif request.command is BrowserWorkerCommand.CANCEL:
        adapter.cancel()
        return BrowserWorkerResponse(
            request_id=request.request_id,
            success=True,
            reason_code="BROWSER_WORKER_CANCELLED",
        )
    elif request.command is BrowserWorkerCommand.CLOSE:
        adapter.close()
        return BrowserWorkerResponse(
            request_id=request.request_id,
            success=True,
            reason_code="BROWSER_WORKER_CLOSED",
        )
    else:
        raise ValueError("Command blocked")
    return BrowserWorkerResponse(
        request_id=request.request_id,
        success=True,
        reason_code="BROWSER_WORKER_OK",
        payload_type=payload_type,
        payload_json=payload.model_dump_json(),
    )


def _request_id_or_zero(line: str) -> UUID:
    """Avoid reflecting untrusted input; all-zero UUID is a non-authoritative error correlation."""
    try:
        return BrowserWorkerRequest.model_validate_json(line).request_id
    except (ValidationError, ValueError):
        return UUID("00000000-0000-0000-0000-000000000000")


if __name__ == "__main__":
    raise SystemExit(run_worker())
