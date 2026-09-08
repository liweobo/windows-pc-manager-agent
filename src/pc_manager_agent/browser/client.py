"""Main-process client for the disposable Browser Worker."""

from __future__ import annotations

import os
import queue
import subprocess  # nosec B404
import sys
import threading
from pathlib import Path
from types import TracebackType
from typing import Self

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

# The sole child is the fixed current-Python Browser Worker; no caller supplies executable/argv.


class BrowserWorkerError(RuntimeError):
    """Raised when the isolated worker fails closed or violates its protocol."""


class BrowserWorkerClient:
    """Serialize one action at a time to a non-elevated, secret-scrubbed subprocess."""

    def __init__(self, *, timeout_seconds: float = 60.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Worker timeout must be positive")
        self._timeout_seconds = timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[str | None] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False

    def start(self, *, headless: bool = False) -> BrowserSessionDescriptor:
        """Launch the worker with no shell and create one ephemeral browser context."""
        if self._process is not None:
            raise BrowserWorkerError("BROWSER_WORKER_ALREADY_STARTED")
        command = _worker_command()
        # Fixed source-module or sibling packaged Worker only; shell remains disabled.
        process = subprocess.Popen(  # nosec B603
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="strict",
            bufsize=1,
            shell=False,
            env=_worker_environment(),
            creationflags=_worker_creation_flags(),
        )
        self._process = process
        self._reader = threading.Thread(target=self._read_responses, daemon=True)
        self._reader.start()
        response = self._exchange(
            BrowserWorkerRequest(command=BrowserWorkerCommand.START, headless=headless)
        )
        return _parse_session(response)

    def navigate(self, url: str, *, allow_http: bool = False) -> BrowserObservation:
        """Navigate to one URL already reviewed by the Main safety layer."""
        response = self._exchange(
            BrowserWorkerRequest(
                command=BrowserWorkerCommand.NAVIGATE,
                url=url,
                allow_insecure_http=allow_http,
            )
        )
        return _parse_observation(response)

    def observe(self) -> BrowserObservation:
        """Read one bounded worker observation."""
        return _parse_observation(
            self._exchange(BrowserWorkerRequest(command=BrowserWorkerCommand.OBSERVE))
        )

    def perform(self, action: BrowserActionRequest) -> BrowserActionResult:
        """Send one validated finite semantic action."""
        response = self._exchange(
            BrowserWorkerRequest(
                command=BrowserWorkerCommand.PERFORM,
                action_json=action.model_dump_json(),
            )
        )
        if response.payload_type is not BrowserWorkerPayloadType.ACTION_RESULT:
            raise BrowserWorkerError("BROWSER_WORKER_ACTION_PAYLOAD_INVALID")
        if response.payload_json is None:
            raise BrowserWorkerError("BROWSER_WORKER_ACTION_PAYLOAD_MISSING")
        return BrowserActionResult.model_validate_json(response.payload_json)

    def download(
        self, action: BrowserActionRequest, temporary_directory: Path
    ) -> BrowserWorkerDownload:
        """Request one confirmed document download into an Agent-owned temporary root."""
        response = self._exchange(
            BrowserWorkerRequest(
                command=BrowserWorkerCommand.DOWNLOAD,
                action_json=action.model_dump_json(),
                temporary_directory=str(temporary_directory.resolve(strict=True)),
            )
        )
        if response.payload_type is not BrowserWorkerPayloadType.DOWNLOAD:
            raise BrowserWorkerError("BROWSER_WORKER_DOWNLOAD_PAYLOAD_INVALID")
        if response.payload_json is None:
            raise BrowserWorkerError("BROWSER_WORKER_DOWNLOAD_PAYLOAD_MISSING")
        return BrowserWorkerDownload.model_validate_json(response.payload_json)

    def cancel(self) -> None:
        """Request context cancellation; a timeout terminates only this owned worker."""
        if self._process is None or self._closed:
            return
        try:
            self._exchange(BrowserWorkerRequest(command=BrowserWorkerCommand.CANCEL))
        except BrowserWorkerError:
            self._terminate_owned_worker()

    def close(self) -> None:
        """Close normally, then terminate only the owned worker if it does not exit."""
        if self._closed:
            return
        process = self._process
        if process is not None and process.poll() is None:
            try:
                self._exchange(BrowserWorkerRequest(command=BrowserWorkerCommand.CLOSE))
                process.wait(timeout=5.0)
            except (BrowserWorkerError, subprocess.TimeoutExpired):
                self._terminate_owned_worker()
        self._close_pipes()
        self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _exchange(self, request: BrowserWorkerRequest) -> BrowserWorkerResponse:
        with self._lock:
            process = self._process
            if self._closed or process is None or process.stdin is None:
                raise BrowserWorkerError("BROWSER_WORKER_NOT_ACTIVE")
            if process.poll() is not None:
                raise BrowserWorkerError("BROWSER_WORKER_EXITED")
            try:
                process.stdin.write(request.model_dump_json() + "\n")
                process.stdin.flush()
                line = self._responses.get(timeout=self._timeout_seconds)
            except (OSError, queue.Empty) as exc:
                self._terminate_owned_worker()
                raise BrowserWorkerError("BROWSER_WORKER_TIMEOUT_OR_PIPE_FAILURE") from exc
            if line is None:
                raise BrowserWorkerError("BROWSER_WORKER_CLOSED_PIPE")
            response = BrowserWorkerResponse.model_validate_json(line)
            if response.request_id != request.request_id:
                raise BrowserWorkerError("BROWSER_WORKER_RESPONSE_MISMATCH")
            if not response.success:
                raise BrowserWorkerError(response.reason_code)
            return response

    def _read_responses(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self._responses.put(None)
            return
        try:
            for line in process.stdout:
                self._responses.put(line)
        finally:
            self._responses.put(None)

    def _terminate_owned_worker(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
        self._close_pipes()

    def _close_pipes(self) -> None:
        """Release parent pipe handles after the owned worker has exited."""
        process = self._process
        if process is None:
            return
        if self._reader is not None:
            self._reader.join(timeout=1.0)
        for stream in (process.stdin, process.stdout):
            if stream is not None and not stream.closed:
                stream.close()


def _parse_session(response: BrowserWorkerResponse) -> BrowserSessionDescriptor:
    if (
        response.payload_type is not BrowserWorkerPayloadType.SESSION
        or response.payload_json is None
    ):
        raise BrowserWorkerError("BROWSER_WORKER_SESSION_PAYLOAD_INVALID")
    return BrowserSessionDescriptor.model_validate_json(response.payload_json)


def _parse_observation(response: BrowserWorkerResponse) -> BrowserObservation:
    if (
        response.payload_type is not BrowserWorkerPayloadType.OBSERVATION
        or response.payload_json is None
    ):
        raise BrowserWorkerError("BROWSER_WORKER_OBSERVATION_PAYLOAD_INVALID")
    return BrowserObservation.model_validate_json(response.payload_json)


def _worker_environment() -> dict[str, str]:
    """Remove credential-like environment variables before Python worker startup."""
    blocked_markers = (
        "API_KEY",
        "PASSWORD",
        "SECRET",
        "TOKEN",
        "COOKIE",
        "OPENAI",
        "AUTHORIZATION",
        "CREDENTIAL",
    )
    return {
        name: value
        for name, value in os.environ.items()
        if not any(marker in name.upper() for marker in blocked_markers)
    }


def _worker_command() -> list[str]:
    """Return only the source interpreter entry or fixed sibling packaged Worker."""
    if bool(getattr(sys, "frozen", False)):
        worker = Path(sys.executable).resolve(strict=True).parent / (
            "browser-worker/pc-manager-browser-worker.exe"
        )
        if not worker.is_file():
            raise BrowserWorkerError("BROWSER_WORKER_BINARY_MISSING")
        return [str(worker)]
    return [sys.executable, "-I", "-m", "pc_manager_agent.browser.worker"]


def _worker_creation_flags() -> int:
    """Suppress a console window for the owned packaged worker on Windows."""
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if sys.platform == "win32" else 0
