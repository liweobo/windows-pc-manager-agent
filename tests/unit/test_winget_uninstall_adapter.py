"""Unit coverage for the exact argv, no-shell winget process adapter."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from pc_manager_agent.domain.winget_uninstall import (
    ValidatedWingetUninstallAction,
    WingetProcessResultCategory,
)
from pc_manager_agent.platform_support.windows.winget_uninstall import (
    WindowsWingetPackageInventoryPlatform,
    WindowsWingetUninstallPlatform,
    _sanitized_environment,
)
from pc_manager_agent.tools.manifest import CancellationToken
from tests.fixtures.winget_uninstall import (
    FakeWingetAvailabilityPlatform,
    executable_identity,
    package,
)


class _Process:
    pid = 123

    def __init__(self, token: CancellationToken | None = None) -> None:
        self._token = token

    def poll(self) -> int | None:
        if self._token is not None:
            self._token.cancel()
            return None
        return 0


def _action(path: Path) -> ValidatedWingetUninstallAction:
    return ValidatedWingetUninstallAction(
        transaction_id=uuid4(),
        package_identity=package().identity,
        software_identity_digest="b" * 64,
        executable_identity=executable_identity(path),
    )


def test_adapter_uses_exact_array_executable_shell_false_and_sanitized_env(
    tmp_path: Path,
) -> None:
    alias = tmp_path / "WindowsApps" / "winget.exe"
    identity = executable_identity(alias)
    captured: dict[str, object] = {}

    def popen(arguments: tuple[str, ...], **kwargs: object) -> _Process:
        captured["arguments"] = arguments
        captured.update(kwargs)
        return _Process()

    platform = WindowsWingetUninstallPlatform(
        FakeWingetAvailabilityPlatform(identity),
        process_factory=popen,
        environment={"SYSTEMROOT": r"C:\Windows", "OPENAI_API_KEY": "secret", "PATH": "bad"},
    )
    result = platform.uninstall(_action(alias), CancellationToken())

    assert result.category is WingetProcessResultCategory.EXITED_ZERO
    assert captured["arguments"] == (
        str(alias),
        "uninstall",
        "--id",
        "Example.CleanApp",
        "--exact",
        "--source",
        "winget",
        "--version",
        "1.0.0",
        "--scope",
        "user",
        "--interactive",
        "--disable-interactivity",
    )
    assert captured["executable"] == str(alias)
    assert captured["shell"] is False
    assert captured["env"] == {"SYSTEMROOT": r"C:\Windows"}


def test_changed_alias_identity_blocks_launch(tmp_path: Path) -> None:
    alias = tmp_path / "winget.exe"
    availability = FakeWingetAvailabilityPlatform(
        executable_identity(alias).model_copy(update={"alias_sha256": "c" * 64})
    )
    called = False

    def popen(arguments: tuple[str, ...], **kwargs: object) -> _Process:
        nonlocal called
        called = True
        return _Process()

    result = WindowsWingetUninstallPlatform(
        availability,
        process_factory=popen,
    ).uninstall(_action(alias), CancellationToken())
    assert result.category is WingetProcessResultCategory.LAUNCH_FAILED
    assert not called


def test_cancel_before_launch_and_after_launch_never_terminate(tmp_path: Path) -> None:
    alias = tmp_path / "winget.exe"
    availability = FakeWingetAvailabilityPlatform(executable_identity(alias))
    before = CancellationToken()
    before.cancel()
    called = False

    def no_call(arguments: tuple[str, ...], **kwargs: object) -> _Process:
        nonlocal called
        called = True
        return _Process()

    result = WindowsWingetUninstallPlatform(
        availability,
        process_factory=no_call,
    ).uninstall(_action(alias), before)
    assert result.category is WingetProcessResultCategory.CANCELLED_BEFORE_LAUNCH
    assert not called

    after = CancellationToken()
    result = WindowsWingetUninstallPlatform(
        availability,
        poll_seconds=0,
        process_factory=lambda args, **kwargs: _Process(after),
    ).uninstall(_action(alias), after)
    assert result.category is WingetProcessResultCategory.MONITORING_STOPPED
    assert result.monitoring_stopped_after_launch


def test_environment_allowlist_drops_tokens_path_and_winget_configuration() -> None:
    assert _sanitized_environment(
        {
            "SYSTEMROOT": r"C:\Windows",
            "USERPROFILE": r"C:\Users\Person",
            "OPENAI_API_KEY": "secret",
            "PATH": "untrusted",
            "WINGET_SOURCE": "custom",
        }
    ) == {"SYSTEMROOT": r"C:\Windows", "USERPROFILE": r"C:\Users\Person"}


def test_package_inventory_requests_only_the_official_source(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    alias = tmp_path / "WindowsApps" / "winget.exe"
    availability = FakeWingetAvailabilityPlatform(executable_identity(alias))
    captured: tuple[str, ...] = ()

    def run(arguments: tuple[str, ...], **kwargs: object) -> SimpleNamespace:
        nonlocal captured
        del kwargs
        captured = arguments
        output = Path(arguments[arguments.index("--output") + 1])
        output.write_text(
            json.dumps(
                {
                    "Sources": [
                        {
                            "SourceDetails": {
                                "Name": "winget",
                                "Identifier": "Microsoft.Winget.Source_8wekyb3d8bbwe",
                            },
                            "Packages": [{"PackageIdentifier": "Example.App", "Version": "1.0"}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        "pc_manager_agent.platform_support.windows.winget_uninstall.subprocess.run",
        run,
    )
    result = WindowsWingetPackageInventoryPlatform(availability, tmp_path).inventory(
        10,
        CancellationToken(),
    )
    assert result.packages[0].package_id == "Example.App"
    assert captured[captured.index("--source") + 1] == "winget"
