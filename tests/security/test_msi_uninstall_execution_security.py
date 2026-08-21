from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from tests.fixtures.msi_uninstall import build_msi_environment
from tests.fixtures.software_analysis import msi_entry
from tests.fixtures.system_diagnostics import FakeSystemPlatform

from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.domain.software_uninstall_execution import MsiPreflightState
from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
from pc_manager_agent.orchestration.software_execution_preflight import (
    SoftwareExecutionPreflight,
)
from pc_manager_agent.orchestration.software_inventory import normalize_raw_entry
from pc_manager_agent.orchestration.software_msi_validation import MsiProductValidator
from pc_manager_agent.orchestration.software_residual_analyzer import SoftwareResidualAnalyzer
from pc_manager_agent.orchestration.software_uninstall_execution import (
    MsiUninstallExecutionError,
)
from pc_manager_agent.tools.manifest import CancellationToken


@pytest.mark.security
@pytest.mark.parametrize(
    "name",
    [
        "Microsoft Visual C++ 2022 Redistributable",
        "Example Device Driver Package",
        "Endpoint Protection Antivirus",
        "Windows PC Manager Agent",
    ],
)
def test_protected_msi_class_never_reaches_adapter(tmp_path: Path, name: str) -> None:
    environment = build_msi_environment(
        tmp_path / "state.sqlite3",
        entry=msi_entry(name=name),
    )
    try:
        with pytest.raises(MsiUninstallExecutionError):
            environment.services.service.prepare(
                f"卸载软件 {name}",
                SoftwareTargetQuery(display_name=name),
            )
        assert environment.adapter.calls == []
    finally:
        environment.close()


@pytest.mark.security
def test_raw_uninstall_string_is_not_forwarded_to_adapter_or_audit(tmp_path: Path) -> None:
    malicious = 'cmd.exe /c "powershell -Command Remove-Item C:\\*"'
    raw = msi_entry().model_copy(update={"uninstall_string": malicious})
    environment = build_msi_environment(tmp_path / "state.sqlite3", entry=raw)
    try:
        prepared = environment.services.service.prepare(
            "卸载软件 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        plan_confirmation = environment.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        runtime = environment.services.service.prepare_runtime_confirmation(
            plan_confirmation.confirmation_id,
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
        assert len(environment.adapter.calls) == 1
        assert not hasattr(environment.adapter.calls[0], "uninstall_string")
        serialized = "\n".join(
            str((row.plan, row.parameters, row.result, row.error))
            for row in environment.audit.list_recent(100)
        )
        assert "powershell" not in serialized.casefold()
        assert "Remove-Item" not in serialized
    finally:
        environment.close()


@pytest.mark.security
def test_identity_change_after_plan_confirmation_aborts_before_adapter(tmp_path: Path) -> None:
    environment = build_msi_environment(tmp_path / "state.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载软件 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        plan_confirmation = environment.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        environment.software_inventory.entries = (msi_entry(version="2.0"),)
        with pytest.raises(MsiUninstallExecutionError):
            environment.services.service.prepare_runtime_confirmation(
                plan_confirmation.confirmation_id,
                prepared.plan,
            )
        assert environment.adapter.calls == []
    finally:
        environment.close()


@pytest.mark.security
def test_incomplete_inventory_aborts_before_confirmation_or_adapter(tmp_path: Path) -> None:
    environment = build_msi_environment(tmp_path / "state.sqlite3")
    environment.software_inventory.truncated = True
    try:
        with pytest.raises(MsiUninstallExecutionError):
            environment.services.service.prepare(
                "卸载软件 Example App",
                SoftwareTargetQuery(display_name="Example App"),
            )
        assert environment.adapter.calls == []
    finally:
        environment.close()


@pytest.mark.security
def test_related_process_and_service_block_without_control_calls(tmp_path: Path) -> None:
    raw = msi_entry()
    software = normalize_raw_entry(raw)
    assert software is not None
    environment = build_msi_environment(tmp_path / "state.sqlite3")
    try:
        registration = environment.msi_inventory.registration
        product = MsiProductValidator(environment.msi_inventory).validate(
            software,
            UninstallCapabilityResolver().resolve(software, raw),
        )
        result = SoftwareExecutionPreflight(FakeSystemPlatform()).inspect(
            software,
            product,
            CancellationToken(),
        )
        assert result.state is MsiPreflightState.BLOCKED
        assert result.related_processes
        assert result.related_services
        assert registration.installed
        assert environment.adapter.calls == []
    finally:
        environment.close()


@pytest.mark.security
def test_residual_analysis_never_deletes_or_recurses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    location = tmp_path / "residual"
    location.mkdir()
    (location / "user-data.txt").write_text("synthetic", encoding="utf-8")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Residual analysis attempted deletion")

    monkeypatch.setattr(Path, "unlink", forbidden)
    monkeypatch.setattr(Path, "rmdir", forbidden)
    monkeypatch.setattr(shutil, "rmtree", forbidden)
    report = SoftwareResidualAnalyzer().analyze(location)
    assert report.install_location_present
    assert not report.deletion_performed
    assert (location / "user-data.txt").exists()
