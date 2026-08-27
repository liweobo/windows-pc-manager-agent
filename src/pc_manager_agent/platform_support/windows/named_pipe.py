"""One-client local Windows named-pipe transport for the Stage 4X2 Broker."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from functools import partial
from typing import TypeVar, cast

import pywintypes
import win32api
import win32con
import win32file
import win32pipe
import win32security

from pc_manager_agent.privileged.ipc_protocol import (
    BrokerIpcDisconnectedError,
    BrokerIpcError,
    BrokerIpcTimeoutError,
)

_PIPE_PREFIX = r"\\.\pipe\WindowsPCManagerAgent.Stage4X2."
_RENDEZVOUS_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_SECURITY_IDENTIFICATION = 0x00010000
_ERROR_BROKEN_PIPE = 109
_ERROR_PIPE_CONNECTED = 535
_ERROR_NO_DATA = 232
_BUFFER_BYTES = 65_536
T = TypeVar("T")


class WindowsNamedPipeError(BrokerIpcError):
    """Raised for a Windows pipe setup or security failure."""


class WindowsNamedPipeStream:
    """Owned connected byte-mode pipe handle with bounded blocking operations."""

    def __init__(self, handle: int, *, server_end: bool) -> None:
        self._handle = handle
        self._server_end = server_end
        self._closed = False
        self._lock = threading.Lock()

    @property
    def native_handle(self) -> int:
        """Return the handle solely for OS peer identity APIs."""
        return self._handle

    @property
    def peer_process_id(self) -> int:
        """Return the PID supplied by the named-pipe filesystem, not by a payload."""
        try:
            value = (
                win32pipe.GetNamedPipeClientProcessId(self._handle)
                if self._server_end
                else win32pipe.GetNamedPipeServerProcessId(self._handle)
            )
            return int(value)
        except pywintypes.error as exc:
            raise WindowsNamedPipeError("Named-pipe peer PID is unavailable") from exc

    @property
    def peer_session_id(self) -> int:
        """Return the OS-derived peer Windows session identifier."""
        try:
            value = (
                win32pipe.GetNamedPipeClientSessionId(self._handle)
                if self._server_end
                else win32pipe.GetNamedPipeServerSessionId(self._handle)
            )
            return int(value)
        except pywintypes.error as exc:
            raise WindowsNamedPipeError("Named-pipe peer session is unavailable") from exc

    def read_exact(self, size: int, *, timeout_seconds: float) -> bytes:
        """Read exactly one bounded byte count or fail on timeout/disconnect."""
        if size < 0 or size > 1_048_576:
            raise ValueError("Named-pipe read size is outside the fixed bound")
        deadline = time.monotonic() + timeout_seconds
        output = bytearray()
        while len(output) < size:
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                raise BrokerIpcTimeoutError("Named-pipe read timed out")
            chunk = self._bounded_call(
                lambda: cast(bytes, win32file.ReadFile(self._handle, size - len(output))[1]),
                timeout_seconds=remaining_time,
            )
            if not chunk:
                raise BrokerIpcDisconnectedError("Named-pipe peer disconnected")
            output.extend(chunk)
        return bytes(output)

    def write_all(self, data: bytes, *, timeout_seconds: float) -> None:
        """Write one already bounded frame without accepting mutable buffers."""
        if not data or len(data) > 1_048_580:
            raise ValueError("Named-pipe write size is outside the fixed bound")
        deadline = time.monotonic() + timeout_seconds
        offset = 0
        while offset < len(data):
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                raise BrokerIpcTimeoutError("Named-pipe write timed out")
            pending = data[offset:]
            written = self._bounded_call(
                partial(self._write_chunk, pending), timeout_seconds=remaining_time
            )
            if written <= 0:
                raise BrokerIpcDisconnectedError("Named-pipe write made no progress")
            offset += written

    def close(self) -> None:
        """Close the pipe handle once and unblock pending I/O."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            with suppress(pywintypes.error):
                if self._server_end:
                    win32pipe.DisconnectNamedPipe(self._handle)
            with suppress(pywintypes.error):
                win32api.CloseHandle(self._handle)

    def _bounded_call(self, operation: Callable[[], T], *, timeout_seconds: float) -> T:
        if self._closed:
            raise BrokerIpcDisconnectedError("Named-pipe stream is closed")
        completed = threading.Event()
        result: list[T] = []
        failure: list[BaseException] = []

        def invoke() -> None:
            try:
                result.append(operation())
            except BaseException as exc:  # transported to the owning thread
                failure.append(exc)
            finally:
                completed.set()

        worker = threading.Thread(target=invoke, name="stage4x2-pipe-io", daemon=True)
        worker.start()
        if not completed.wait(timeout_seconds):
            self.close()
            raise BrokerIpcTimeoutError("Named-pipe operation timed out")
        if failure:
            exc = failure[0]
            if isinstance(exc, pywintypes.error) and exc.winerror in {
                _ERROR_BROKEN_PIPE,
                _ERROR_NO_DATA,
            }:
                raise BrokerIpcDisconnectedError("Named-pipe peer disconnected") from exc
            raise WindowsNamedPipeError("Named-pipe I/O failed") from exc
        return result[0]

    def _write_chunk(self, value: bytes) -> int:
        return int(win32file.WriteFile(self._handle, value)[1])


class WindowsBrokerPipeServer:
    """Elevated one-instance pipe server with an explicit single-user DACL."""

    def __init__(self, rendezvous_id: str, user_sid: str) -> None:
        self._name = pipe_name(rendezvous_id)
        self._handle: int | None = _create_server_handle(self._name, user_sid)
        self._accepted = False

    def accept(self, *, timeout_seconds: float) -> WindowsNamedPipeStream:
        """Accept exactly one local client and permanently reject additional clients."""
        if self._accepted:
            raise WindowsNamedPipeError("Broker pipe already accepted its only client")
        if self._handle is None:
            raise WindowsNamedPipeError("Broker pipe endpoint is closed")
        handle = self._handle
        completed = threading.Event()
        failure: list[BaseException] = []

        def connect() -> None:
            try:
                win32pipe.ConnectNamedPipe(handle, None)
            except pywintypes.error as exc:
                if exc.winerror != _ERROR_PIPE_CONNECTED:
                    failure.append(exc)
            finally:
                completed.set()

        worker = threading.Thread(target=connect, name="stage4x2-pipe-accept", daemon=True)
        worker.start()
        if not completed.wait(timeout_seconds):
            self.close()
            raise BrokerIpcTimeoutError("Broker pipe accept timed out")
        if failure:
            raise WindowsNamedPipeError("Broker pipe accept failed") from failure[0]
        self._accepted = True
        stream = WindowsNamedPipeStream(handle, server_end=True)
        self._handle = None
        return stream

    def close(self) -> None:
        """Close an unaccepted endpoint; accepted streams own their handle."""
        if self._handle is not None:
            with suppress(pywintypes.error):
                win32api.CloseHandle(self._handle)
            self._handle = None


class WindowsBrokerPipeClient:
    """Standard-user connector for one exact local Broker endpoint."""

    def __init__(self, rendezvous_id: str) -> None:
        self._name = pipe_name(rendezvous_id)

    def connect(self, *, timeout_seconds: float) -> WindowsNamedPipeStream:
        """Wait for and open the unique endpoint with identification-only SQOS."""
        timeout_ms = max(1, min(int(timeout_seconds * 1_000), 120_000))
        try:
            win32pipe.WaitNamedPipe(self._name, timeout_ms)
            handle = win32file.CreateFile(
                self._name,
                win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                0,
                None,
                win32con.OPEN_EXISTING,
                win32con.SECURITY_SQOS_PRESENT | _SECURITY_IDENTIFICATION,
                None,
            )
        except pywintypes.error as exc:
            if exc.winerror in {2, 121}:  # not found / semaphore timeout
                raise BrokerIpcTimeoutError("Broker pipe was not ready before timeout") from exc
            raise WindowsNamedPipeError("Broker pipe connection failed") from exc
        return WindowsNamedPipeStream(cast(int, handle), server_end=False)


def pipe_name(rendezvous_id: str) -> str:
    """Derive one local endpoint name only from a fixed prefix and opaque ID."""
    if not _RENDEZVOUS_PATTERN.fullmatch(rendezvous_id):
        raise ValueError("Broker rendezvous ID is malformed")
    return f"{_PIPE_PREFIX}{rendezvous_id}"


def _create_server_handle(name: str, user_sid: str) -> int:
    try:
        sid = win32security.ConvertStringSidToSid(user_sid)
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION,
            win32con.GENERIC_READ | win32con.GENERIC_WRITE,
            sid,
        )
        descriptor = win32security.SECURITY_DESCRIPTOR()
        descriptor.Initialize()
        descriptor.SetSecurityDescriptorDacl(True, dacl, False)
        attributes = pywintypes.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = descriptor
        attributes.bInheritHandle = False
        return win32pipe.CreateNamedPipe(
            name,
            win32pipe.PIPE_ACCESS_DUPLEX | win32pipe.FILE_FLAG_FIRST_PIPE_INSTANCE,
            win32pipe.PIPE_TYPE_BYTE
            | win32pipe.PIPE_READMODE_BYTE
            | win32pipe.PIPE_WAIT
            | win32pipe.PIPE_REJECT_REMOTE_CLIENTS,
            1,
            _BUFFER_BYTES,
            _BUFFER_BYTES,
            0,
            attributes,
        )
    except pywintypes.error as exc:
        raise WindowsNamedPipeError("Secure Broker pipe creation failed") from exc
