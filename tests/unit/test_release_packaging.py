"""Static and synthetic tests for isolated Stage 7A release packaging."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from scripts.evaluate_release_gate import main as evaluate_release_gate
from scripts.generate_third_party_notices import render_notices
from scripts.generate_version_info import generate_all, read_version, render_version_info
from scripts.inspect_release_artifacts import (
    ArtifactInspectionError,
    inspect_broker_xref,
    inspect_directory,
)

from pc_manager_agent.browser import client as browser_client
from pc_manager_agent.browser.client import BrowserWorkerError
from pc_manager_agent.release.gate import ReadinessLevel, ReleaseGateEvaluator

_ROOT = Path(__file__).resolve().parents[2]


def test_version_resources_come_from_the_single_package_version(tmp_path: Path) -> None:
    source = _ROOT / "src" / "pc_manager_agent" / "version.py"

    generated = generate_all(source, tmp_path)

    assert read_version(source) == "1.0.0-rc.1"
    assert len(generated) == 3
    for path in generated:
        content = path.read_text(encoding="utf-8")
        assert "filevers=(1, 0, 0, 1)" in content
        assert "ProductVersion', '1.0.0-rc.1'" in content
    invalid = tmp_path / "missing-version.py"
    invalid.write_text("VERSION = 'invalid'", encoding="utf-8")
    with pytest.raises(ValueError, match="VERSION_SOURCE_INVALID"):
        read_version(invalid)


def test_version_renderer_binds_each_original_filename() -> None:
    rendered = render_version_info("1.2.3", "fixed.exe", "Fixed Product")
    assert "OriginalFilename', 'fixed.exe'" in rendered
    assert "FileDescription', 'Fixed Product'" in rendered


def test_manifests_keep_main_and_worker_as_invoker() -> None:
    manifests = _ROOT / "packaging" / "manifests"
    for name in (
        "pc-manager-agent.exe.manifest",
        "pc-manager-privileged-broker.exe.manifest",
        "pc-manager-browser-worker.exe.manifest",
    ):
        content = (manifests / name).read_text(encoding="utf-8")
        assert 'level="asInvoker"' in content
        assert "requireAdministrator" not in content
    main = (manifests / "pc-manager-agent.exe.manifest").read_text(encoding="utf-8")
    assert "PerMonitorV2" in main
    assert "longPathAware" in main


def test_specs_are_separate_and_production_main_excludes_mock() -> None:
    packaging = _ROOT / "packaging"
    main = (packaging / "pc-manager-agent.spec").read_text(encoding="utf-8")
    broker = (packaging / "pc-manager-privileged-broker.spec").read_text(encoding="utf-8")
    worker = (packaging / "pc-manager-browser-worker.spec").read_text(encoding="utf-8")
    assert "runtime_hooks=[str(production_hook)]" in main
    assert '"pc_manager_agent.privileged.mock_broker"' in main
    assert 'name="pc-manager-agent"' in main
    assert 'name="pc-manager-privileged-broker"' in broker
    assert 'name="pc-manager-browser-worker"' in worker
    assert 'ambient_icu_names = {"icuuc.dll", "icudt78.dll"}' in main
    for forbidden in ("PySide6", "openai", "pc_manager_agent.ui"):
        assert f'"{forbidden}"' in broker
    assert '"pc_manager_agent.broker"' in worker


def test_installer_uses_program_files_and_preserves_user_state() -> None:
    installer = (_ROOT / "packaging" / "installer" / "windows-pc-manager-agent.iss").read_text(
        encoding="utf-8"
    )
    assert "DefaultDirName={autopf}" in installer
    assert "PrivilegesRequired=admin" in installer
    assert "PrivilegesRequiredOverridesAllowed" not in installer
    assert "{app}\\broker" in installer
    assert "\n[UninstallDelete]\n" not in installer
    assert "LocalAppData" in installer
    assert "runasoriginaluser" in installer
    assert 'Name: "english"; MessagesFile: "compiler:Default.isl"' in installer
    assert "ChineseSimplified.isl" not in installer


def test_installer_lifecycle_runs_isolated_production_smoke() -> None:
    lifecycle = (_ROOT / "scripts" / "test-installer-lifecycle.ps1").read_text(encoding="utf-8")
    layout = (_ROOT / "scripts" / "test-installed-layout.ps1").read_text(encoding="utf-8")
    assert "REQUIRES_EXPLICIT_EPHEMERAL_GITHUB_HOST" in lifecycle
    assert "Invoke-CheckedProcess $main @('--version')" in lifecycle
    assert "Invoke-CheckedProcess $main @('--smoke-test')" in lifecycle
    assert "GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])" in layout
    assert "S-1-1-0" in layout
    assert "S-1-5-11" in layout
    assert "S-1-5-32-545" in layout
    assert ".Translate(" not in layout
    assert "WriteData" in layout
    assert "AppendData" in layout
    assert "ChangePermissions" in layout
    assert "TakeOwnership" in layout
    assert "FileSystemRights]::FullControl" not in layout


def test_release_workflow_is_read_only_and_branch_scoped_before_merge() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "release-candidate.yml").read_text(
        encoding="utf-8"
    )
    assert "contents: read" in workflow
    assert "workflow_dispatch:" in workflow
    assert "- codex/stage-7a-production-hardening" in workflow
    assert "choco list --exact innosetup --limit-output" in workflow
    assert ".ToLowerInvariant() -ne 'innosetup|6.7.1'" in workflow
    assert "INNO_VERSION_DRIFT" in workflow
    assert "VersionInfo.ProductVersion" not in workflow
    assert "release:" not in workflow


def test_signing_hook_requires_store_identity_and_verification() -> None:
    script = (_ROOT / "scripts" / "sign-artifacts.ps1").read_text(encoding="utf-8")
    assert "SIGNING_NOT_CONFIGURED" in script
    assert "signtool" in script.casefold()
    assert "Get-AuthenticodeSignature" in script
    assert ".pfx" not in script.casefold()
    assert "password" not in script.casefold()


def test_artifact_inspector_accepts_minimal_outputs_and_blocks_state_or_secret(
    tmp_path: Path,
) -> None:
    root = tmp_path / "main"
    root.mkdir()
    (root / "pc-manager-agent.exe").write_bytes(b"MZ synthetic")
    (root / "safe.dll").write_bytes(b"binary")

    evidence = inspect_directory(root, "pc-manager-agent.exe")

    assert evidence.files == 2
    assert evidence.bytes > 0
    assert len(evidence.sha256) == 64
    (root / "state.db").write_bytes(b"SQLite")
    with pytest.raises(ArtifactInspectionError, match="ARTIFACT_FORBIDDEN_FILE"):
        inspect_directory(root, "pc-manager-agent.exe")
    (root / "state.db").unlink()
    synthetic_token = b"sk-" + b"synthetic-not-a-real-key"
    (root / "safe.dll").write_bytes(b"prefix " + synthetic_token + b" suffix")
    with pytest.raises(ArtifactInspectionError, match="ARTIFACT_SECRET_MARKER"):
        inspect_directory(root, "pc-manager-agent.exe")


def test_artifact_inspector_allows_public_certificate_and_blocks_private_key(
    tmp_path: Path,
) -> None:
    root = tmp_path / "main"
    root.mkdir()
    (root / "pc-manager-agent.exe").write_bytes(b"MZ synthetic")
    certificate = root / "cacert.pem"
    certificate.write_bytes(
        b"-----BEGIN CERTIFICATE-----\n" + b"A" * 64 + b"\n-----END CERTIFICATE-----\n"
    )

    inspect_directory(root, "pc-manager-agent.exe")

    private_key = root / "private.pem"
    begin = b"-----BEGIN " + b"PRIVATE KEY-----\n"
    end = b"-----END " + b"PRIVATE KEY-----\n"
    private_key.write_bytes(begin + b"A" * 64 + b"\n" + end)
    with pytest.raises(ArtifactInspectionError, match="ARTIFACT_PRIVATE_KEY"):
        inspect_directory(root, "pc-manager-agent.exe")

    private_key.unlink()
    (root / "icuuc.dll").write_bytes(b"ambient build dependency")
    with pytest.raises(ArtifactInspectionError, match="ARTIFACT_AMBIENT_DLL"):
        inspect_directory(root, "pc-manager-agent.exe")


def test_runtime_notices_include_runtime_and_exclude_development_dependencies() -> None:
    notices = render_notices("windows-pc-manager-agent")
    package_rows = notices.casefold()

    assert "| pydantic |" in package_rows
    assert "| pyside6 |" in package_rows
    assert "| pytest |" not in package_rows
    assert "| ruff |" not in package_rows
    assert "| pyinstaller |" not in package_rows


def test_executable_release_gate_fails_missing_evidence_and_passes_exact_set(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        evaluate_release_gate(
            ["--requested", "DEV_READY", "--reference", "test", "--passed", "quality"]
        )
        == 1
    )
    assert '"passed": false' in capsys.readouterr().out
    required = sorted(
        check.value for check in ReleaseGateEvaluator.required_checks(ReadinessLevel.DEV_READY)
    )
    assert (
        evaluate_release_gate(
            [
                "--requested",
                "DEV_READY",
                "--reference",
                "test",
                "--passed",
                *required,
            ]
        )
        == 0
    )
    assert '"passed": true' in capsys.readouterr().out


def test_broker_xref_rejects_model_or_mock_dependencies(tmp_path: Path) -> None:
    xref = tmp_path / "xref.html"
    xref.write_text("pc_manager_agent.privileged.service_handler", encoding="utf-8")
    inspect_broker_xref(xref)
    xref.write_text("pc_manager_agent.privileged.mock_broker", encoding="utf-8")
    with pytest.raises(ArtifactInspectionError, match="BROKER_DEPENDENCY_FORBIDDEN"):
        inspect_broker_xref(xref)


def test_packaged_browser_worker_uses_only_fixed_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main = tmp_path / "pc-manager-agent.exe"
    main.write_bytes(b"main")
    monkeypatch.setattr(sys, "executable", str(main))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(BrowserWorkerError, match="BROWSER_WORKER_BINARY_MISSING"):
        browser_client._worker_command()
    worker = tmp_path / "browser-worker" / "pc-manager-browser-worker.exe"
    worker.parent.mkdir()
    worker.write_bytes(b"worker")

    assert browser_client._worker_command() == [str(worker)]


def test_source_browser_worker_command_isolated_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert browser_client._worker_command() == [
        sys.executable,
        "-I",
        "-m",
        "pc_manager_agent.browser.worker",
    ]
