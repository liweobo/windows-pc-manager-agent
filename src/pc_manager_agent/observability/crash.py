"""Local-only sanitized crash evidence and repeated-unclean-start safe-mode trigger."""

from __future__ import annotations

import json
import os
import sys
import traceback
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from uuid import uuid4

from pc_manager_agent.observability.logging import LogRedactionPolicy
from pc_manager_agent.safety.path_policy import is_reparse_point


class CrashEvidenceError(RuntimeError):
    """Raised when local crash state cannot be written without weakening privacy."""


@dataclass(frozen=True, slots=True)
class CrashLoopDecision:
    """Reduction-only startup decision derived from recent unclean sessions."""

    previous_session_unclean: bool
    recent_unclean_starts: int
    safe_mode_recommended: bool


class LocalCrashReporter:
    """Write bounded sanitized exception metadata locally and never upload it."""

    def __init__(
        self,
        data_directory: Path,
        app_version: str,
        *,
        redaction: LogRedactionPolicy | None = None,
        max_reports: int = 10,
    ) -> None:
        if max_reports < 1 or max_reports > 50:
            raise ValueError("max_reports must be between 1 and 50")
        self._directory = data_directory / "crash-reports"
        self._app_version = app_version
        self._redaction = redaction or LogRedactionPolicy()
        self._max_reports = max_reports

    def capture(
        self,
        exception_type: type[BaseException],
        exception: BaseException,
        traceback_value: TracebackType | None,
    ) -> Path:
        """Persist type/message and hashed frames without source lines, locals, or user bodies."""
        self._directory.mkdir(parents=True, exist_ok=True)
        if is_reparse_point(self._directory):
            raise CrashEvidenceError("CRASH_REPORT_DIRECTORY_UNSAFE")
        frames = []
        if traceback_value is not None:
            frames = [
                {
                    "file": self._redaction.path_reference(Path(frame.filename)),
                    "line": frame.lineno,
                    "function": _safe_function_name(frame.name),
                }
                for frame in traceback.extract_tb(traceback_value, limit=50)
            ]
        payload = {
            "schema_version": 1,
            "timestamp": datetime.now(UTC).isoformat(),
            "app_version": self._app_version,
            "exception": {
                "type": exception_type.__name__,
                "message": self._redaction.redact_text(str(exception)),
            },
            "frames": frames,
            "automatically_uploaded": False,
        }
        target = self._directory / f"crash-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex}.json"
        try:
            with target.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise CrashEvidenceError("CRASH_REPORT_WRITE_FAILED") from exc
        self._prune()
        return target

    def install(
        self, *, chain_previous: bool = False
    ) -> Callable[[type[BaseException], BaseException, TracebackType | None], None]:
        """Install a privacy-preserving hook; chaining is explicit and development-only."""
        previous = sys.excepthook

        def report(
            exception_type: type[BaseException],
            exception: BaseException,
            traceback_value: TracebackType | None,
        ) -> None:
            with suppress(CrashEvidenceError):
                self.capture(exception_type, exception, traceback_value)
            if chain_previous:
                previous(exception_type, exception, traceback_value)

        sys.excepthook = report
        return previous

    def _prune(self) -> None:
        reports = sorted(
            (
                item
                for item in self._directory.glob("crash-*.json")
                if not is_reparse_point(item) and item.is_file()
            ),
            key=lambda item: item.lstat().st_mtime_ns,
            reverse=True,
        )
        for expired in reports[self._max_reports :]:
            expired.unlink(missing_ok=True)


class CrashLoopGuard:
    """Recommend a reduction-only safe mode after three recent unclean sessions."""

    def __init__(
        self,
        data_directory: Path,
        *,
        threshold: int = 3,
        window_seconds: int = 600,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if threshold < 2 or threshold > 10 or window_seconds < 60 or window_seconds > 3_600:
            raise ValueError("Crash loop limits are outside the supported policy")
        self._directory = data_directory / "health"
        self._active_marker = self._directory / "active-session.json"
        self._history_path = self._directory / "unclean-starts.json"
        self._threshold = threshold
        self._window_seconds = window_seconds
        self._clock = clock or (lambda: datetime.now(UTC).timestamp())

    def begin_session(self) -> CrashLoopDecision:
        """Persist this startup and count only prior sessions lacking a clean-exit record."""
        self._directory.mkdir(parents=True, exist_ok=True)
        if is_reparse_point(self._directory):
            raise CrashEvidenceError("CRASH_LOOP_DIRECTORY_UNSAFE")
        now = self._clock()
        previous_unclean = self._active_marker.exists()
        history = self._read_history(now)
        if previous_unclean:
            history.append(now)
        history = [item for item in history if now - self._window_seconds <= item <= now]
        self._atomic_json(self._history_path, {"unclean_starts": history})
        self._atomic_json(self._active_marker, {"started_at": now})
        return CrashLoopDecision(
            previous_session_unclean=previous_unclean,
            recent_unclean_starts=len(history),
            safe_mode_recommended=len(history) >= self._threshold,
        )

    def mark_clean_exit(self) -> None:
        """Clear only Agent-owned crash-loop markers after orderly runtime shutdown."""
        self._active_marker.unlink(missing_ok=True)
        self._atomic_json(self._history_path, {"unclean_starts": []})

    def _read_history(self, now: float) -> list[float]:
        if not self._history_path.exists():
            return []
        try:
            value = json.loads(self._history_path.read_text(encoding="utf-8"))
            items = value["unclean_starts"]
            if not isinstance(items, list):
                raise TypeError
            return [float(item) for item in items if 0 <= float(item) <= now]
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            # Corrupt health metadata only removes capabilities; it cannot grant authority.
            return [now] * self._threshold

    def _atomic_json(self, destination: Path, payload: dict[str, object]) -> None:
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise CrashEvidenceError("CRASH_LOOP_STATE_WRITE_FAILED") from exc


def _safe_function_name(value: str) -> str:
    if value.replace("_", "").replace("<", "").replace(">", "").isalnum():
        return value[:128]
    return "unknown"
