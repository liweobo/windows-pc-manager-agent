from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.software_uninstall_analysis import canonical_digest
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiInstallContext,
    MsiInstallerResultCategory,
    ValidatedMsiProduct,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareArchitecture, SoftwareScope
from pc_manager_agent.orchestration.msi_exit_codes import map_msi_exit_code
from pc_manager_agent.platform_support.windows.msi_uninstall import WindowsMsiUninstallPlatform
from pc_manager_agent.tools.manifest import CancellationToken


class FakeProcess:
    def __init__(self, exit_code: int, running_polls: int = 0) -> None:
        self.exit_code = exit_code
        self.running_polls = running_polls

    def poll(self) -> int | None:
        if self.running_polls:
            self.running_polls -= 1
            return None
        return self.exit_code


def _product() -> ValidatedMsiProduct:
    code = "{12345678-1234-1234-1234-1234567890AB}"
    digest = canonical_digest(code)
    return ValidatedMsiProduct(
        product_code=code,
        product_code_digest=digest,
        identity_digest="1" * 64,
        metadata_digest="2" * 64,
        capability_digest="3" * 64,
        registration_digest="4" * 64,
        install_context=MsiInstallContext.USER_UNMANAGED,
        display_name="Example App",
        display_version="1.0",
        publisher="Example Publisher",
        scope=SoftwareScope.CURRENT_USER,
        architecture=SoftwareArchitecture.X64,
        source_anchor_digest="5" * 64,
    )


@pytest.mark.parametrize(
    ("code", "category"),
    [
        (0, MsiInstallerResultCategory.SUCCESS),
        (3010, MsiInstallerResultCategory.SUCCESS_REBOOT_REQUIRED),
        (1641, MsiInstallerResultCategory.REBOOT_INITIATED_UNEXPECTED),
        (1602, MsiInstallerResultCategory.USER_CANCELLED),
        (1618, MsiInstallerResultCategory.ANOTHER_INSTALL_IN_PROGRESS),
        (5, MsiInstallerResultCategory.PRIVILEGE_REQUIRED),
        (1625, MsiInstallerResultCategory.POLICY_BLOCKED),
        (1603, MsiInstallerResultCategory.INSTALLER_FAILURE),
        (9999, MsiInstallerResultCategory.UNKNOWN),
    ],
)
def test_exit_code_mapper(code: int, category: MsiInstallerResultCategory) -> None:
    assert map_msi_exit_code(code) is category


def test_adapter_uses_fixed_executable_args_and_shell_false(tmp_path: Path) -> None:
    executable = tmp_path / "msiexec.exe"
    executable.touch()
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def popen(*args: object, **kwargs: object) -> FakeProcess:
        calls.append((args, kwargs))
        return FakeProcess(3010)

    adapter = WindowsMsiUninstallPlatform(
        system_directory=tmp_path,
        popen_factory=popen,
        poll_interval_seconds=0.01,
        long_running_seconds=1,
    )
    result = adapter.uninstall(_product(), CancellationToken())
    args, kwargs = calls[0]
    assert args[0] == [
        str(executable.resolve()),
        "/x",
        "{12345678-1234-1234-1234-1234567890AB}",
        "/norestart",
    ]
    assert kwargs["shell"] is False
    assert result.category is MsiInstallerResultCategory.SUCCESS_REBOOT_REQUIRED


def test_adapter_cancellation_before_launch_never_spawns(tmp_path: Path) -> None:
    calls = 0

    def popen(*args: object, **kwargs: object) -> FakeProcess:
        nonlocal calls
        calls += 1
        return FakeProcess(0)

    token = CancellationToken()
    token.cancel()
    result = WindowsMsiUninstallPlatform(
        system_directory=tmp_path,
        popen_factory=popen,
    ).uninstall(_product(), token)
    assert not result.launched
    assert result.cancellation_requested_before_launch
    assert calls == 0


def test_adapter_detaches_monitoring_without_terminating_hung_installer(
    tmp_path: Path,
) -> None:
    (tmp_path / "msiexec.exe").touch()
    current = 0.0
    process = FakeProcess(0, running_polls=100)

    def monotonic() -> float:
        return current

    def sleeper(seconds: float) -> None:
        nonlocal current
        current += seconds

    result = WindowsMsiUninstallPlatform(
        system_directory=tmp_path,
        popen_factory=lambda *args, **kwargs: process,
        poll_interval_seconds=0.25,
        long_running_seconds=1.0,
        monotonic=monotonic,
        sleeper=sleeper,
    ).uninstall(_product(), CancellationToken())
    assert result.category is MsiInstallerResultCategory.MONITORING_DETACHED
    assert result.long_running_observed
    assert result.launched
    assert not hasattr(process, "terminate")


def test_msi_tool_risk_is_irreversible_r2() -> None:
    from pc_manager_agent.tools.system_tools.software_uninstall import MsiUninstallTool

    class NeverCalled:
        def uninstall(self, product: ValidatedMsiProduct, cancellation: CancellationToken) -> None:
            raise AssertionError("not called")

    manifest = MsiUninstallTool(NeverCalled()).manifest  # type: ignore[arg-type]
    assert manifest.risk_level is RiskLevel.R2
    assert manifest.irreversible
    assert manifest.max_batch_size == 1
    assert manifest.requires_runtime_confirmation
