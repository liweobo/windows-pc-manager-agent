from __future__ import annotations

import os
from pathlib import Path

import pytest

from pc_manager_agent.domain.reports import ScanRequest
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.tools.file_tools.scanner import DirectoryScannerTool
from pc_manager_agent.tools.manifest import CancellationToken


def make_scanner(root: Path) -> DirectoryScannerTool:
    return DirectoryScannerTool(PathPolicy.for_scan_root(root))


def test_scanner_reads_metadata_and_honors_exclusion(tmp_path: Path) -> None:
    included = tmp_path / "included.txt"
    included.write_text("hello", encoding="utf-8")
    excluded_directory = tmp_path / "excluded"
    excluded_directory.mkdir()
    (excluded_directory / "hidden.txt").write_text("hidden", encoding="utf-8")
    report = make_scanner(tmp_path).execute(
        ScanRequest(root=tmp_path, excluded_paths=(excluded_directory,), max_files=10),
        CancellationToken(),
    )
    assert report.summary.files_seen == 1
    assert report.files[0].name == "included.txt"
    assert report.files[0].size_bytes == 5
    assert report.summary.total_size_bytes == 5


def test_scanner_enforces_file_limit_timeout_and_cancellation(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"{index}.txt").write_text(str(index), encoding="utf-8")
    scanner = make_scanner(tmp_path)
    limited = scanner.execute(ScanRequest(root=tmp_path, max_files=1), CancellationToken())
    assert limited.summary.truncated
    assert limited.summary.files_seen == 1

    ticks = iter((10.0, 10.1))
    timed_scanner = DirectoryScannerTool(
        PathPolicy.for_scan_root(tmp_path), clock=lambda: next(ticks, 10.1)
    )
    timed = timed_scanner.execute(
        ScanRequest(root=tmp_path, timeout_seconds=0.05), CancellationToken()
    )
    assert timed.summary.timed_out

    token = CancellationToken()
    token.cancel()
    cancelled = scanner.execute(ScanRequest(root=tmp_path), token)
    assert cancelled.summary.cancelled
    assert cancelled.summary.files_seen == 0


def test_scanner_reads_nested_directory_with_stable_identity(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "inside.txt").write_text("x", encoding="utf-8")

    report = make_scanner(tmp_path).execute(ScanRequest(root=tmp_path), CancellationToken())

    assert report.summary.files_seen == 1
    assert report.files[0].path == nested / "inside.txt"
    assert not any(issue.code == "path-identity-changed" for issue in report.issues)


def test_scanner_skips_symlink_when_supported(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "inside.txt").write_text("x", encoding="utf-8")
    link = tmp_path / "link"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Windows symlink creation is unavailable: {exc}")
    report = make_scanner(tmp_path).execute(ScanRequest(root=tmp_path), CancellationToken())
    assert any(issue.code == "reparse-point" and issue.path == link for issue in report.issues)
    assert report.summary.files_seen == 1


def test_scanner_converts_permission_failure_to_issue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scanner = make_scanner(tmp_path)

    def deny(_path: Path) -> object:
        raise PermissionError("denied")

    monkeypatch.setattr("pc_manager_agent.tools.file_tools.scanner.os.scandir", deny)
    report = scanner.execute(ScanRequest(root=tmp_path), CancellationToken())
    assert report.summary.issues == 1
    assert report.issues[0].code == "permission-denied"


def test_scanner_stops_when_directory_identity_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scanner = make_scanner(tmp_path)
    original_identity = scanner._directory_identity(tmp_path)
    calls = 0

    def changed_identity(_path: Path) -> tuple[int, int]:
        nonlocal calls
        calls += 1
        return original_identity if calls == 1 else (original_identity[0], original_identity[1] + 1)

    monkeypatch.setattr(scanner, "_directory_identity", changed_identity)
    report = scanner.execute(ScanRequest(root=tmp_path), CancellationToken())
    assert report.summary.files_seen == 0
    assert report.issues[0].code == "path-identity-changed"
