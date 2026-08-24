"""Static and model-level Stage 4D2C2 security regression checks."""

from __future__ import annotations

import inspect

from pydantic import BaseModel

from pc_manager_agent.platform_support.windows import msix_packages
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.system_tools.msix_uninstall import MsixUninstallTool


def test_adapter_has_no_powershell_all_users_or_provisioned_remove() -> None:
    """The production adapter exposes only the reviewed current-user WinRT call."""
    source = inspect.getsource(msix_packages.WindowsMsixPackagePlatform).casefold()
    assert "subprocess" not in source
    assert "start-process" not in source
    assert "remove_for_all_users" not in source
    assert "find_provisioned_packages" not in source
    assert "deprovision" not in source
    assert "remove_package_with_options_async" in source
    assert "preserve_roamable_application_data" in source


def test_tool_has_no_data_deletion_process_or_service_control() -> None:
    """The registered write tool cannot recursively delete data or control lifecycle objects."""
    source = inspect.getsource(MsixUninstallTool).casefold()
    for forbidden in ("rmtree", ".unlink", "terminate", "kill", "stop_service", "runas"):
        assert forbidden not in source


def test_tool_rejects_every_untyped_request() -> None:
    """A generic Pydantic payload cannot reach the narrow adapter."""

    class UntrustedRequest(BaseModel):
        value: str = "untrusted"

    tool = MsixUninstallTool(object())  # type: ignore[arg-type]
    try:
        tool.execute(UntrustedRequest(), CancellationToken())
    except TypeError as exc:
        assert "unexpected input" in str(exc)
    else:
        raise AssertionError("untyped request reached the MSIX tool")
