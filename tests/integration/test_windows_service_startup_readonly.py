"""Windows-only read integration checks; this module never changes a service."""

import os

import pytest

from pc_manager_agent.domain.service_actions import ServiceStartupType
from pc_manager_agent.platform_support.windows.service_control import (
    WindowsServiceControlPlatform,
)
from pc_manager_agent.platform_support.windows.service_startup import (
    WindowsServiceStartupPlatform,
)


@pytest.mark.skipif(os.name != "nt", reason="Windows SCM integration only")
def test_real_scm_startup_configuration_and_permissions_are_read_only() -> None:
    control = WindowsServiceControlPlatform()
    observations = control.list_services(max_items=5)
    if not observations:
        pytest.skip("No queryable Win32 service is available")
    observation = observations[0]
    assert observation.startup_configuration.startup_type in set(ServiceStartupType)
    assert isinstance(observation.startup_configuration.delayed_auto_start, bool)
    permissions = WindowsServiceStartupPlatform().evaluate_permissions(
        observation.identity.service_name
    )
    assert isinstance(permissions.can_query_configuration, bool)
    assert isinstance(permissions.can_change_configuration, bool)
    assert isinstance(permissions.process_elevated, bool)
