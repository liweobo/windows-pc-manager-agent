from __future__ import annotations

import os
import sys

import psutil
import pytest

from pc_manager_agent.browser.client import BrowserWorkerClient, _worker_environment
from pc_manager_agent.domain.browser import BrowserActionKind, BrowserActionRequest


@pytest.mark.windows
@pytest.mark.playwright
@pytest.mark.skipif(sys.platform != "win32", reason="Stage 5C production target is Windows")
def test_real_worker_client_lifecycle_is_typed_and_non_resumable() -> None:
    client = BrowserWorkerClient(timeout_seconds=30)
    owned_processes: list[psutil.Process] = []
    with client:
        session = client.start(headless=True)
        observation = client.observe()
        result = client.perform(
            BrowserActionRequest(
                session_id=session.session_id,
                page_id=observation.page_id,
                navigation_id=observation.navigation_id,
                kind=BrowserActionKind.OBSERVE,
            )
        )
        assert result.completed is True
        assert result.session_id == session.session_id
        assert client._process is not None
        worker = psutil.Process(client._process.pid)
        owned_processes = [worker, *worker.children(recursive=True)]
        client.cancel()
    _, alive = psutil.wait_procs(owned_processes, timeout=10)
    assert alive == []


def test_worker_environment_drops_secret_like_names(monkeypatch) -> None:
    monkeypatch.setenv("PC_MANAGER_SYNTHETIC_TOKEN", "must-not-cross")
    monkeypatch.setenv("PC_MANAGER_SYNTHETIC_ORDINARY", "safe")
    environment = _worker_environment()
    assert "PC_MANAGER_SYNTHETIC_TOKEN" not in environment
    assert environment["PC_MANAGER_SYNTHETIC_ORDINARY"] == "safe"
    assert os.environ["PC_MANAGER_SYNTHETIC_TOKEN"] == "must-not-cross"
