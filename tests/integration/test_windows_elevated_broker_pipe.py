"""Real local named-pipe ACL and bounded roundtrip without UAC or service writes."""

from __future__ import annotations

import threading

import pytest
import win32api
import win32con
import win32security

from pc_manager_agent.platform_support.windows.named_pipe import (
    WindowsBrokerPipeClient,
    WindowsBrokerPipeServer,
)
from pc_manager_agent.privileged.ipc_protocol import new_opaque_id

pytestmark = pytest.mark.windows


def _current_user_sid() -> str:
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        user = win32security.GetTokenInformation(token, win32security.TokenUser)
        return str(win32security.ConvertSidToStringSid(user[0]))
    finally:
        win32api.CloseHandle(token)


def test_named_pipe_uses_exact_user_dacl_and_one_local_roundtrip() -> None:
    sid = _current_user_sid()
    rendezvous = new_opaque_id()
    server = WindowsBrokerPipeServer(rendezvous, sid)
    accepted: list[object] = []
    failure: list[BaseException] = []

    def accept() -> None:
        try:
            accepted.append(server.accept(timeout_seconds=5))
        except BaseException as exc:
            failure.append(exc)

    worker = threading.Thread(target=accept)
    worker.start()
    client = WindowsBrokerPipeClient(rendezvous).connect(timeout_seconds=5)
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert not failure
    stream = accepted[0]
    try:
        descriptor = win32security.GetSecurityInfo(
            stream.native_handle,  # type: ignore[attr-defined]
            win32security.SE_KERNEL_OBJECT,
            win32security.DACL_SECURITY_INFORMATION,
        )
        dacl = descriptor.GetSecurityDescriptorDacl()
        assert dacl is not None
        assert dacl.GetAceCount() == 1
        ace = dacl.GetAce(0)
        assert str(win32security.ConvertSidToStringSid(ace[2])) == sid
        assert client.peer_process_id == win32api.GetCurrentProcessId()
        assert stream.peer_process_id == win32api.GetCurrentProcessId()  # type: ignore[attr-defined]

        client.write_all(b"main-to-broker", timeout_seconds=2)
        assert stream.read_exact(14, timeout_seconds=2) == b"main-to-broker"  # type: ignore[attr-defined]
        stream.write_all(b"broker-to-main", timeout_seconds=2)  # type: ignore[attr-defined]
        assert client.read_exact(14, timeout_seconds=2) == b"broker-to-main"
    finally:
        client.close()
        stream.close()  # type: ignore[attr-defined]
        server.close()
