"""Security regression coverage for the Stage 4D2C1 execution boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.fixtures.winget_uninstall import build_winget_environment, package, winget_entry

from pc_manager_agent.confirmation.winget_uninstall import WingetUninstallConfirmationError
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.domain.winget_uninstall import OFFICIAL_WINGET_SOURCE_IDENTIFIER
from pc_manager_agent.orchestration.winget_uninstall_execution import (
    WingetUninstallExecutionError,
)


@pytest.mark.security
def test_package_version_toctou_invalidates_runtime_and_never_dispatches(tmp_path: Path) -> None:
    environment = build_winget_environment(tmp_path / "state.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example Clean App",
            SoftwareTargetQuery(display_name="Example Clean App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        parent = environment.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        environment.package_inventory.packages = (package(version="2.0.0"),)
        with pytest.raises((WingetUninstallExecutionError, ValueError)):
            environment.services.service.prepare_runtime_confirmation(
                parent.confirmation_id,
                prepared.plan,
            )
        assert environment.adapter.calls == []
    finally:
        environment.close()


@pytest.mark.security
def test_source_change_and_software_mapping_change_are_blocked(tmp_path: Path) -> None:
    environment = build_winget_environment(tmp_path / "source.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example Clean App",
            SoftwareTargetQuery(display_name="Example Clean App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        parent = environment.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        changed_identity = package().identity.model_copy(
            update={"source_identifier": OFFICIAL_WINGET_SOURCE_IDENTIFIER[:-1] + "x"}
        )
        environment.package_inventory.packages = (
            package().model_copy(update={"identity": changed_identity}),
        )
        with pytest.raises((WingetUninstallExecutionError, ValueError)):
            environment.services.service.prepare_runtime_confirmation(
                parent.confirmation_id,
                prepared.plan,
            )
        assert environment.adapter.calls == []
    finally:
        environment.close()

    mapping = build_winget_environment(tmp_path / "mapping.sqlite3")
    try:
        prepared = mapping.services.service.prepare(
            "卸载 Example Clean App",
            SoftwareTargetQuery(display_name="Example Clean App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        parent = mapping.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        mapping.software_inventory.entries = (
            winget_entry(
                mapping.raw.install_location,
                package_id="Different.Package",
            ),
        )
        with pytest.raises((WingetUninstallExecutionError, ValueError)):
            mapping.services.service.prepare_runtime_confirmation(
                parent.confirmation_id,
                prepared.plan,
            )
        assert mapping.adapter.calls == []
    finally:
        mapping.close()


@pytest.mark.security
def test_consumed_confirmation_cannot_be_replayed(tmp_path: Path) -> None:
    environment = build_winget_environment(tmp_path / "state.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example Clean App",
            SoftwareTargetQuery(display_name="Example Clean App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        parent = environment.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        runtime = environment.services.service.prepare_runtime_confirmation(
            parent.confirmation_id,
            prepared.plan,
        )
        environment.services.service.resolve_runtime_confirmation(
            runtime.confirmation.confirmation_id,
            True,
            prepared.plan,
            runtime.preview,
        )
        environment.services.service.execute(
            runtime.confirmation.confirmation_id,
            prepared.plan,
            runtime.preview,
        )
        with pytest.raises(WingetUninstallConfirmationError):
            environment.services.service.execute(
                runtime.confirmation.confirmation_id,
                prepared.plan,
                runtime.preview,
            )
        assert len(environment.adapter.calls) == 1
    finally:
        environment.close()


@pytest.mark.security
def test_source_files_have_no_shell_elevation_force_restart_or_cleanup_escape_hatch() -> None:
    root = Path(__file__).parents[2] / "src" / "pc_manager_agent"
    adapter = (root / "platform_support" / "windows" / "winget_uninstall.py").read_text(
        encoding="utf-8"
    )
    tool = (root / "tools" / "system_tools" / "winget_uninstall.py").read_text(encoding="utf-8")
    orchestration = (root / "orchestration" / "winget_uninstall_execution.py").read_text(
        encoding="utf-8"
    )
    combined = adapter + tool + orchestration
    assert "shell=False" in adapter
    assert 'name="software.uninstall.winget"' in tool
    for forbidden in (
        "shell=True",
        "ShellExecute",
        "runas",
        "Remove-AppxPackage",
        "--override",
        "--silent",
        "--force",
        "--purge",
        ".terminate(",
        ".kill(",
    ):
        assert forbidden not in combined


@pytest.mark.security
def test_elevated_agent_is_blocked_before_any_winget_dispatch(tmp_path: Path) -> None:
    environment = build_winget_environment(tmp_path / "elevated.sqlite3")
    try:
        environment.services.service._process_is_elevated = lambda: True
        with pytest.raises(WingetUninstallExecutionError, match="elevated"):
            environment.services.service.prepare(
                "卸载 Example Clean App",
                SoftwareTargetQuery(display_name="Example Clean App"),
            )
        assert environment.adapter.calls == []
    finally:
        environment.close()
