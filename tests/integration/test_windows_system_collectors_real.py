from __future__ import annotations

import sys

import pytest

from pc_manager_agent.platform_support.windows.system_diagnostics import (
    WindowsSystemDiagnosticsPlatform,
)
from pc_manager_agent.tools.manifest import CancellationToken


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only collectors")
def test_real_windows_query_only_collectors_return_bounded_metadata() -> None:
    platform = WindowsSystemDiagnosticsPlatform()
    token = CancellationToken()
    info = platform.collect_system_info()
    cpu = platform.collect_cpu(2, 0.1, token)
    memory = platform.collect_memory()
    disks, _disk_warnings = platform.collect_disks()
    processes, _process_warnings = platform.collect_processes(0.1, 2_000, token)
    startup, _startup_warnings, _startup_truncated = platform.collect_startup(5_000)
    services, _service_warnings, _service_truncated = platform.collect_services(5_000)
    software, _software_warnings, _software_truncated = platform.collect_software(5_000)

    assert info.computer_name
    assert len(cpu.samples) == 2
    assert memory.total_bytes > 0
    assert disks
    assert processes.processes
    assert services
    assert all(not hasattr(item, "command_line") for item in processes.processes)
    assert all(not hasattr(item, "uninstall_command") for item in software)
    assert len(startup) <= 5_000
