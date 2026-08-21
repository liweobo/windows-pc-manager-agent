"""Unit coverage for the shell-free Vendor process adapter and environment isolation."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.domain.software_uninstall_analysis import canonical_digest
from pc_manager_agent.domain.vendor_uninstall import (
    ValidatedVendorUninstallAction,
    VendorArgumentAssessment,
    VendorArgumentDecision,
    VendorAuthenticodeEvidence,
    VendorAuthenticodeStatus,
    VendorExecutableFileIdentity,
    VendorExecutableObservation,
    VendorExecutableTrustAssessment,
    VendorInstallLocationRelation,
    VendorProcessResultCategory,
    VendorPublisherMatch,
    VendorTrustDecision,
    VendorUninstallerIdentity,
)
from pc_manager_agent.platform_support.windows.vendor_uninstall import (
    WindowsVendorExecutablePlatform,
    WindowsVendorUninstallPlatform,
    sanitized_vendor_environment,
)
from pc_manager_agent.tools.manifest import CancellationToken


class _Process:
    pid = 321

    def __init__(self, results: list[int | None]) -> None:
        self._results = iter(results)

    def poll(self) -> int | None:
        return next(self._results)


def _action(path: Path) -> ValidatedVendorUninstallAction:
    metadata = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    argument_assessment = VendorArgumentAssessment(
        decision=VendorArgumentDecision.ALLOW,
        argument_fingerprint=canonical_digest(("/remove",)),
        argument_count=1,
        reasons=("allowed",),
    )
    identity = VendorUninstallerIdentity(
        software_identity_hash="a" * 64,
        registry_source_fingerprint="b" * 64,
        command_metadata_digest="c" * 64,
        executable=VendorExecutableObservation(
            file_identity=VendorExecutableFileIdentity(
                executable_path=path,
                volume_serial=1,
                file_id="01",
                size_bytes=metadata.st_size,
                created_ns=metadata.st_ctime_ns,
                modified_ns=metadata.st_mtime_ns,
                attributes=0,
                sha256=digest,
            ),
            local_fixed_volume=True,
            reparse_free=True,
            blocked_location=False,
            install_location_relation=VendorInstallLocationRelation.INSIDE_INSTALL_LOCATION,
            authenticode=VendorAuthenticodeEvidence(
                status=VendorAuthenticodeStatus.VALID,
                signer_subject="Example",
            ),
            publisher_match=VendorPublisherMatch.MATCHED,
        ),
        arguments=("/remove",),
        argument_assessment=argument_assessment,
        trust=VendorExecutableTrustAssessment(
            decision=VendorTrustDecision.TRUSTED_FOR_EXECUTION,
            reasons=("trusted",),
            evidence=("valid signature",),
        ),
    )
    return ValidatedVendorUninstallAction(
        software_identity_hash="a" * 64,
        vendor_identity=identity,
        transaction_id=uuid4(),
    )


def test_adapter_uses_exact_array_shell_false_and_sanitized_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / "uninstall.exe"
    executable.write_bytes(b"safe fixture")
    captured: dict[str, object] = {}

    def popen(arguments: list[str], **kwargs: object) -> _Process:
        captured["arguments"] = arguments
        captured.update(kwargs)
        return _Process([0])

    monkeypatch.setattr(
        "pc_manager_agent.platform_support.windows.vendor_uninstall._child_processes",
        lambda pid: set(),
    )
    platform = WindowsVendorUninstallPlatform(
        popen_factory=popen,
        environ={"SYSTEMROOT": r"C:\Windows", "OPENAI_API_KEY": "secret"},
    )

    result = platform.uninstall(_action(executable), CancellationToken())

    assert result.category is VendorProcessResultCategory.PROCESS_EXITED_ZERO
    assert captured["arguments"] == [str(executable), "/remove"]
    assert captured["shell"] is False
    assert captured["cwd"] == str(executable.parent)
    assert captured["env"] == {"SYSTEMROOT": r"C:\Windows"}


def test_adapter_detects_replaced_executable_before_launch(tmp_path: Path) -> None:
    executable = tmp_path / "uninstall.exe"
    executable.write_bytes(b"before")
    action = _action(executable)
    executable.write_bytes(b"after")
    called = False

    def popen(arguments: list[str], **kwargs: object) -> _Process:
        nonlocal called
        called = True
        return _Process([0])

    result = WindowsVendorUninstallPlatform(popen_factory=popen).uninstall(
        action,
        CancellationToken(),
    )
    assert result.category is VendorProcessResultCategory.LAUNCH_FAILED
    assert not called


def test_executable_inspection_rejects_original_reparse_spelling_before_resolve(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install = tmp_path / "example"
    install.mkdir()
    executable = install / "uninstall.exe"
    executable.write_bytes(b"fixture")
    monkeypatch.setattr(
        "pc_manager_agent.platform_support.windows.vendor_uninstall._reparse_free",
        lambda path: False,
    )

    with pytest.raises(PermissionError, match="reparse"):
        WindowsVendorExecutablePlatform().inspect(executable, install, "Example")


def test_environment_builder_drops_secrets_and_unapproved_variables() -> None:
    assert sanitized_vendor_environment(
        {
            "SYSTEMROOT": r"C:\Windows",
            "USERPROFILE": r"C:\Users\Person",
            "MY_TOKEN": "secret",
            "PATH": "untrusted",
            "TEMP": "bad\x00value",
        }
    ) == {"SYSTEMROOT": r"C:\Windows", "USERPROFILE": r"C:\Users\Person"}


def test_adapter_cancellation_before_launch_never_creates_process(tmp_path: Path) -> None:
    executable = tmp_path / "uninstall.exe"
    executable.write_bytes(b"safe fixture")
    token = CancellationToken()
    token.cancel()
    called = False

    def popen(arguments: list[str], **kwargs: object) -> _Process:
        nonlocal called
        called = True
        return _Process([0])

    result = WindowsVendorUninstallPlatform(popen_factory=popen).uninstall(
        _action(executable),
        token,
    )
    assert result.category is VendorProcessResultCategory.CANCELLED_BEFORE_LAUNCH
    assert not result.launched
    assert not called


def test_adapter_stops_monitoring_without_terminating_process(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / "uninstall.exe"
    executable.write_bytes(b"safe fixture")
    token = CancellationToken()

    class CancellingProcess:
        pid = 654

        def poll(self) -> None:
            token.cancel()

    monkeypatch.setattr(
        "pc_manager_agent.platform_support.windows.vendor_uninstall._child_processes",
        lambda pid: set(),
    )
    platform = WindowsVendorUninstallPlatform(
        popen_factory=lambda *args, **kwargs: CancellingProcess(),
        monotonic=lambda: 0.0,
        sleeper=lambda seconds: None,
    )
    result = platform.uninstall(_action(executable), token)

    assert result.category is VendorProcessResultCategory.STOPPED_MONITORING
    assert result.process_id == 654
    assert result.monitoring_stopped_after_launch


def test_adapter_detaches_from_long_running_vendor_ui(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / "uninstall.exe"
    executable.write_bytes(b"safe fixture")
    moments = iter((0.0, 2.0))
    monkeypatch.setattr(
        "pc_manager_agent.platform_support.windows.vendor_uninstall._child_processes",
        lambda pid: set(),
    )
    platform = WindowsVendorUninstallPlatform(
        popen_factory=lambda *args, **kwargs: _Process([None]),
        long_running_seconds=1.0,
        monotonic=lambda: next(moments),
        sleeper=lambda seconds: None,
    )
    result = platform.uninstall(_action(executable), CancellationToken())

    assert result.category is VendorProcessResultCategory.MONITORING_DETACHED
    assert result.process_id == 321
    assert result.long_running_observed


def test_adapter_waits_for_observed_child_after_parent_exit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / "uninstall.exe"
    executable.write_bytes(b"safe fixture")
    child_states = iter((True, False))
    monkeypatch.setattr(
        "pc_manager_agent.platform_support.windows.vendor_uninstall._child_processes",
        lambda pid: {999},
    )
    monkeypatch.setattr(
        "pc_manager_agent.platform_support.windows.vendor_uninstall.psutil.pid_exists",
        lambda pid: next(child_states),
    )
    platform = WindowsVendorUninstallPlatform(
        popen_factory=lambda *args, **kwargs: _Process([0, 0]),
        monotonic=lambda: 0.0,
        sleeper=lambda seconds: None,
    )
    result = platform.uninstall(_action(executable), CancellationToken())

    assert result.category is VendorProcessResultCategory.PROCESS_EXITED_ZERO
    assert result.tracked_child_count == 1


def test_adapter_distinguishes_nonzero_exit_and_vendor_requested_elevation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / "uninstall.exe"
    executable.write_bytes(b"safe fixture")
    monkeypatch.setattr(
        "pc_manager_agent.platform_support.windows.vendor_uninstall._child_processes",
        lambda pid: set(),
    )
    nonzero = WindowsVendorUninstallPlatform(
        popen_factory=lambda *args, **kwargs: _Process([5])
    ).uninstall(_action(executable), CancellationToken())
    assert nonzero.category is VendorProcessResultCategory.PROCESS_EXITED_NONZERO
    assert nonzero.exit_code == 5

    def request_elevation(*args: object, **kwargs: object) -> _Process:
        error = OSError("elevation required")
        error.winerror = 740  # type: ignore[attr-defined]
        raise error

    elevated = WindowsVendorUninstallPlatform(
        popen_factory=request_elevation,
        monotonic=lambda: 0.0,
    ).uninstall(_action(executable), CancellationToken())
    assert elevated.category is VendorProcessResultCategory.VENDOR_REQUESTED_ELEVATION
    assert not elevated.launched
