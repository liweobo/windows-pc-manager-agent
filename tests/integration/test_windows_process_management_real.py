from __future__ import annotations

import subprocess
import sys
import time

import psutil
import pytest

from pc_manager_agent.domain.process_actions import ProcessMemberResultState
from pc_manager_agent.platform_support.windows.process_management import (
    WindowsProcessManagementPlatform,
)


@pytest.mark.windows
def test_real_adapter_inspects_and_force_terminates_only_its_test_child() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    platform = WindowsProcessManagementPlatform()
    try:
        observation = platform.inspect_process(child.pid)
        assert observation is not None
        assert observation.identity.pid == child.pid
        assert observation.identity.create_time is not None
        assert observation.identity.executable_path.exists()
        result = platform.force_terminate(observation.identity, 5)
        assert result.state is ProcessMemberResultState.EXITED
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


@pytest.mark.windows
def test_real_adapter_detects_changed_identity_before_force() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    platform = WindowsProcessManagementPlatform()
    try:
        observation = platform.inspect_process(child.pid)
        assert observation is not None
        tampered = observation.identity.model_copy(
            update={"create_time": observation.identity.create_time.replace(year=2025)}
        )
        result = platform.force_terminate(tampered, 1)
        assert result.state is ProcessMemberResultState.IDENTITY_CHANGED
        assert psutil.pid_exists(child.pid)
    finally:
        if child.poll() is None:
            child.terminate()
        deadline = time.monotonic() + 5
        while child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
