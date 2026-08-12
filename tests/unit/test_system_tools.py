from __future__ import annotations

import pytest

from pc_manager_agent.domain.system_diagnostics import EmptyCollectorRequest
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.system_tools.collectors import (
    CpuTool,
    DiskTool,
    MemoryTool,
    ProcessTool,
    ServiceTool,
    SoftwareTool,
    StartupTool,
    SystemInfoTool,
)
from tests.fixtures.system_diagnostics import FakeSystemPlatform


@pytest.mark.parametrize(
    "tool_type",
    (
        SystemInfoTool,
        CpuTool,
        MemoryTool,
        DiskTool,
        ProcessTool,
        StartupTool,
        ServiceTool,
        SoftwareTool,
    ),
)
def test_system_tools_reject_direct_wrong_input(tool_type: type[object]) -> None:
    tool = tool_type(FakeSystemPlatform())
    with pytest.raises(TypeError, match="unexpected input"):
        tool.execute(
            EmptyCollectorRequest() if tool_type is CpuTool else object(), CancellationToken()
        )
