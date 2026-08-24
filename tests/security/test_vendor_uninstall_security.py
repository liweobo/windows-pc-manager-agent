"""Security regression coverage for the Stage 4D2B execution boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.fixtures.msi_uninstall import build_msi_environment
from tests.fixtures.vendor_uninstall import build_vendor_environment, vendor_entry

from pc_manager_agent.audit.repository import AuditUnavailableError
from pc_manager_agent.confirmation.vendor_uninstall import VendorUninstallConfirmationError
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.domain.vendor_uninstall import VendorUninstallTransactionState
from pc_manager_agent.orchestration.vendor_uninstall_execution import (
    VendorUninstallExecutionError,
)


@pytest.mark.security
@pytest.mark.parametrize(
    "arguments",
    (
        "/quiet",
        "/remove & calc.exe",
        "@answer.txt",
        "/delete-data",
        "/remove /norestart",
    ),
)
def test_dangerous_arguments_never_reach_adapter(tmp_path: Path, arguments: str) -> None:
    install = tmp_path / "example-app"
    install.mkdir()
    (install / "uninstall.exe").write_bytes(b"fixture")
    environment = build_vendor_environment(
        tmp_path / "state.sqlite3",
        entry=vendor_entry(install, arguments=arguments),
    )
    try:
        with pytest.raises(VendorUninstallExecutionError):
            environment.services.service.prepare(
                "卸载 Example App",
                SoftwareTargetQuery(display_name="Example App"),
            )
        assert environment.adapter.calls == []
    finally:
        environment.close()


@pytest.mark.security
def test_executable_replacement_after_plan_invalidates_runtime_confirmation(
    tmp_path: Path,
) -> None:
    environment = build_vendor_environment(tmp_path / "state.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        parent = environment.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        executable = environment.raw.install_location / "uninstall.exe"
        executable.write_bytes(b"replaced after Preview")
        with pytest.raises(
            (VendorUninstallConfirmationError, VendorUninstallExecutionError, ValueError)
        ):
            environment.services.service.prepare_runtime_confirmation(
                parent.confirmation_id,
                prepared.plan,
            )
        assert environment.adapter.calls == []
    finally:
        environment.close()


@pytest.mark.security
def test_raw_command_and_executable_path_never_enter_audit_or_database(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    environment = build_vendor_environment(database)
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.plan and prepared.preview
        audit_text = "\n".join(
            str((row.plan, row.parameters, row.result, row.error))
            for row in environment.audit.list_recent(100)
        )
        database_text = database.read_bytes().decode("utf-8", errors="ignore")
        assert "uninstall.exe" not in audit_text.casefold()
        assert "/remove" not in audit_text.casefold()
        assert "uninstall.exe" not in database_text.casefold()
        assert "/remove" not in database_text.casefold()
    finally:
        environment.close()


@pytest.mark.security
def test_consumed_runtime_confirmation_cannot_be_replayed(tmp_path: Path) -> None:
    environment = build_vendor_environment(tmp_path / "state.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example App",
            SoftwareTargetQuery(display_name="Example App"),
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
        with pytest.raises(VendorUninstallConfirmationError):
            environment.services.service.execute(
                runtime.confirmation.confirmation_id,
                prepared.plan,
                runtime.preview,
            )
        assert len(environment.adapter.calls) == 1
    finally:
        environment.close()


@pytest.mark.security
def test_active_vendor_transaction_blocks_msi_mechanism(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    vendor = build_vendor_environment(database)
    msi = None
    try:
        prepared = vendor.services.service.prepare(
            "卸载 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.plan_confirmation is not None
        msi = build_msi_environment(database)
        with pytest.raises(RuntimeError, match="MSI, Vendor, or winget"):
            msi.services.service.prepare(
                "卸载 Example App",
                SoftwareTargetQuery(display_name="Example App"),
            )
        assert msi.adapter.calls == []
    finally:
        if msi is not None:
            msi.close()
        vendor.close()


@pytest.mark.security
def test_argument_metadata_change_after_plan_invalidates_confirmation(tmp_path: Path) -> None:
    environment = build_vendor_environment(tmp_path / "state.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        parent = environment.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        environment.software_inventory.entries = (
            vendor_entry(environment.raw.install_location, arguments="--uninstall"),
        )
        with pytest.raises(
            (VendorUninstallConfirmationError, VendorUninstallExecutionError, ValueError)
        ):
            environment.services.service.prepare_runtime_confirmation(
                parent.confirmation_id,
                prepared.plan,
            )
        assert environment.adapter.calls == []
    finally:
        environment.close()


@pytest.mark.security
def test_mandatory_prelaunch_audit_failure_prevents_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = build_vendor_environment(tmp_path / "state.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example App",
            SoftwareTargetQuery(display_name="Example App"),
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

        def fail_audit(*args: object, **kwargs: object) -> None:
            raise AuditUnavailableError("synthetic audit failure")

        monkeypatch.setattr(environment.services.service._audit, "started", fail_audit)
        with pytest.raises(AuditUnavailableError):
            environment.services.service.execute(
                runtime.confirmation.confirmation_id,
                prepared.plan,
                runtime.preview,
            )
        assert environment.adapter.calls == []
        assert environment.repository.state(prepared.plan.transaction_id) is (
            VendorUninstallTransactionState.FAILED
        )
    finally:
        environment.close()


@pytest.mark.security
def test_vendor_execution_sources_have_no_shell_elevation_or_cleanup_escape_hatch() -> None:
    root = Path(__file__).parents[2] / "src" / "pc_manager_agent"
    adapter = (root / "platform_support" / "windows" / "vendor_uninstall.py").read_text(
        encoding="utf-8"
    )
    orchestration = (root / "orchestration" / "vendor_uninstall_execution.py").read_text(
        encoding="utf-8"
    )
    residual = (root / "orchestration" / "vendor_residual_analyzer.py").read_text(encoding="utf-8")

    assert "shell=False" in adapter
    for forbidden in (
        "shell=True",
        "ShellExecute",
        '"runas"',
        "os.system",
        ".terminate(",
        ".kill(",
        "shutdown /r",
    ):
        assert forbidden not in adapter
        assert forbidden not in orchestration
    for forbidden in ("rmtree(", ".unlink(", "Remove-Item", "registry delete"):
        assert forbidden not in residual
