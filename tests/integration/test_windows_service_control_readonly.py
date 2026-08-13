"""Real-Windows read-only SCM tests; no start or stop is ever invoked."""

import sys

import pytest

from pc_manager_agent.domain.service_actions import ServiceActionType
from pc_manager_agent.platform_support.windows.service_control import (
    WindowsServiceControlPlatform,
)


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows SCM is required")
def test_real_service_adapter_only_reads_and_probes_handles() -> None:
    platform = WindowsServiceControlPlatform()
    items = platform.list_services(5)
    assert 0 < len(items) <= 5
    item = items[0]
    current = platform.inspect(item.identity.service_name)
    assert current is not None
    assert current.identity.service_name.casefold() == item.identity.service_name.casefold()
    evidence = platform.evaluate_permissions(item.identity.service_name, ServiceActionType.RESTART)
    assert evidence.can_query
