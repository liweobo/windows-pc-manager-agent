"""Local crash evidence, privacy, retention, and crash-loop tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import pc_manager_agent.observability.crash as crash_module
from pc_manager_agent.observability.crash import (
    CrashEvidenceError,
    CrashLoopGuard,
    LocalCrashReporter,
)
from pc_manager_agent.observability.logging import LogRedactionPolicy


def _captured_error() -> tuple[type[BaseException], BaseException, object]:
    try:
        raise RuntimeError(r"token=secret-value C:\Users\Alice\private.txt")
    except RuntimeError as exc:
        return type(exc), exc, exc.__traceback__


def test_local_crash_report_is_redacted_local_only_and_retained(tmp_path: Path) -> None:
    reporter = LocalCrashReporter(
        tmp_path,
        "0.1.0",
        redaction=LogRedactionPolicy(path_salt=b"fixed"),
        max_reports=2,
    )
    exception_type, exception, traceback_value = _captured_error()

    paths = [
        reporter.capture(exception_type, exception, traceback_value)  # type: ignore[arg-type]
        for _ in range(3)
    ]

    reports = tuple((tmp_path / "crash-reports").glob("crash-*.json"))
    assert len(reports) == 2
    assert not paths[0].exists()
    payload = json.loads(paths[-1].read_text(encoding="utf-8"))
    assert payload["app_version"] == "0.1.0"
    assert payload["automatically_uploaded"] is False
    assert payload["exception"]["type"] == "RuntimeError"
    assert payload["frames"]
    rendered = json.dumps(payload)
    for forbidden in ("secret-value", "Alice", "private.txt", "raise RuntimeError"):
        assert forbidden not in rendered
    assert "[PATH:" in rendered


def test_reporter_without_traceback_and_installed_hook_calls_previous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    def previous(
        exception_type: type[BaseException],
        _exception: BaseException,
        _traceback: object,
    ) -> None:
        observed.append(exception_type.__name__)

    monkeypatch.setattr(sys, "excepthook", previous)
    reporter = LocalCrashReporter(tmp_path, "0.1.0")
    original = reporter.install(chain_previous=True)
    assert original is previous

    sys.excepthook(ValueError, ValueError("password=secret"), None)

    assert observed == ["ValueError"]
    payload = json.loads(next((tmp_path / "crash-reports").glob("*.json")).read_text())
    assert payload["frames"] == []
    assert "secret" not in json.dumps(payload)


def test_reporter_does_not_chain_to_an_unknown_hook_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []
    monkeypatch.setattr(
        sys,
        "excepthook",
        lambda exception_type, _exception, _traceback: observed.append(exception_type.__name__),
    )
    LocalCrashReporter(tmp_path, "0.1.0").install()

    sys.excepthook(ValueError, ValueError("token=do-not-forward"), None)

    assert observed == []
    assert next((tmp_path / "crash-reports").glob("*.json")).is_file()


def test_reporter_rejects_invalid_limit_unsafe_path_and_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="max_reports"):
        LocalCrashReporter(tmp_path, "0.1.0", max_reports=0)

    reporter = LocalCrashReporter(tmp_path / "unsafe", "0.1.0")
    monkeypatch.setattr(crash_module, "is_reparse_point", lambda path: path.name == "crash-reports")
    with pytest.raises(CrashEvidenceError, match="CRASH_REPORT_DIRECTORY_UNSAFE"):
        reporter.capture(ValueError, ValueError("safe"), None)

    monkeypatch.setattr(crash_module, "is_reparse_point", lambda _path: False)
    failing = LocalCrashReporter(tmp_path / "write-failure", "0.1.0")
    monkeypatch.setattr(
        crash_module.json, "dump", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk"))
    )
    with pytest.raises(CrashEvidenceError, match="CRASH_REPORT_WRITE_FAILED"):
        failing.capture(ValueError, ValueError("safe"), None)
    assert tuple((tmp_path / "write-failure" / "crash-reports").iterdir()) == ()


def test_crash_loop_recommends_reduction_only_mode_after_threshold(tmp_path: Path) -> None:
    now = 1_000.0
    decisions = [
        CrashLoopGuard(tmp_path, threshold=3, clock=lambda index=index: now + index).begin_session()
        for index in range(4)
    ]

    assert not decisions[0].previous_session_unclean
    assert decisions[-1].previous_session_unclean
    assert decisions[-1].recent_unclean_starts == 3
    assert decisions[-1].safe_mode_recommended

    CrashLoopGuard(tmp_path, threshold=3, clock=lambda: now + 5).mark_clean_exit()
    clean = CrashLoopGuard(tmp_path, threshold=3, clock=lambda: now + 6).begin_session()
    assert not clean.previous_session_unclean
    assert clean.recent_unclean_starts == 0


def test_old_crashes_expire_and_corrupt_history_fails_to_safe_mode(tmp_path: Path) -> None:
    health = tmp_path / "health"
    health.mkdir()
    (health / "active-session.json").write_text("{}", encoding="utf-8")
    (health / "unclean-starts.json").write_text(
        json.dumps({"unclean_starts": [1.0, 950.0, 1_001.0]}), encoding="utf-8"
    )
    decision = CrashLoopGuard(
        tmp_path, threshold=3, window_seconds=60, clock=lambda: 1_000.0
    ).begin_session()
    assert decision.recent_unclean_starts == 2
    assert not decision.safe_mode_recommended

    (health / "unclean-starts.json").write_text("not-json", encoding="utf-8")
    corrupt = CrashLoopGuard(tmp_path, threshold=3, clock=lambda: 1_001.0).begin_session()
    assert corrupt.safe_mode_recommended


def test_crash_loop_policy_and_storage_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="Crash loop limits"):
        CrashLoopGuard(tmp_path, threshold=1)
    with pytest.raises(ValueError, match="Crash loop limits"):
        CrashLoopGuard(tmp_path, window_seconds=30)

    unsafe = tmp_path / "unsafe"
    monkeypatch.setattr(crash_module, "is_reparse_point", lambda path: path.name == "health")
    with pytest.raises(CrashEvidenceError, match="CRASH_LOOP_DIRECTORY_UNSAFE"):
        CrashLoopGuard(unsafe).begin_session()

    monkeypatch.setattr(crash_module, "is_reparse_point", lambda _path: False)
    guard = CrashLoopGuard(tmp_path / "replace-failure")
    monkeypatch.setattr(
        crash_module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("disk"))
    )
    with pytest.raises(CrashEvidenceError, match="CRASH_LOOP_STATE_WRITE_FAILED"):
        guard.begin_session()
    assert not tuple((tmp_path / "replace-failure" / "health").glob("*.tmp"))
