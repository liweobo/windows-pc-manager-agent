"""Bounded local JSON logging with centralized secret and path redaction."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

from pc_manager_agent.config.production import BuildMode
from pc_manager_agent.safety.path_policy import is_reparse_point

REDACTED: Final = "[REDACTED]"
_SENSITIVE_KEY_PARTS: Final = (
    "api_key",
    "apikey",
    "authorization",
    "audio",
    "broker_secret",
    "confirmation_secret",
    "cookie",
    "credential",
    "document_body",
    "document_content",
    "mfa",
    "oauth",
    "password",
    "private_page",
    "prompt_body",
    "raw_audio",
    "secret",
    "session_data",
    "token",
    "transcript",
    "webpage_body",
    "webpage_content",
)
_ASSIGNMENT_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|authorization|cookie|mfa|oauth[_-]?code|password|secret|token)\b"
    r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}")
_KNOWN_TOKEN = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]{12,}|gh[pousr]_[a-z0-9_]{12,}|"
    r"eyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})\b"
)
_WINDOWS_PATH = re.compile(r"(?i)(?<![a-z0-9])(?:[a-z]:[\\/]|\\\\)[^\s,;\"']+")
_URL_QUERY = re.compile(r"(?i)(https?://[^\s?#]+)\?[^\s#]+")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class LogConfigurationError(RuntimeError):
    """Raised when a safe bounded log destination cannot be established."""


class LogRedactionPolicy:
    """Redact recursively and hash local paths with a per-process salt."""

    def __init__(self, *, path_salt: bytes | None = None, max_text_chars: int = 1_024) -> None:
        if max_text_chars < 64 or max_text_chars > 8_192:
            raise ValueError("max_text_chars must be between 64 and 8192")
        self._path_salt = path_salt or os.urandom(32)
        self._max_text_chars = max_text_chars

    def redact(self, value: object, *, key: str | None = None) -> object:
        """Return JSON-safe metadata without known secrets, bodies, or absolute paths."""
        if key is not None and self.is_sensitive_key(key):
            return REDACTED
        if value is None or isinstance(value, bool | int | float):
            return value
        if isinstance(value, Path):
            return self.path_reference(value)
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, Mapping):
            return {
                str(item_key)[:128]: self.redact(item_value, key=str(item_key))
                for item_key, item_value in value.items()
            }
        if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
            return [self.redact(item) for item in value[:100]]
        return self.redact_text(str(value))

    @staticmethod
    def is_sensitive_key(key: str) -> bool:
        """Recognize a closed conservative set of credential and content field names."""
        normalized = key.casefold().replace("-", "_").replace(" ", "_")
        return any(part in normalized for part in _SENSITIVE_KEY_PARTS)

    def redact_text(self, value: str) -> str:
        """Remove known inline credentials, URL queries, and local absolute paths."""
        sanitized = _BEARER.sub(f"Bearer {REDACTED}", value)
        sanitized = _ASSIGNMENT_SECRET.sub(lambda match: f"{match.group(1)}={REDACTED}", sanitized)
        sanitized = _KNOWN_TOKEN.sub(REDACTED, sanitized)
        sanitized = _URL_QUERY.sub(r"\1?[REDACTED_QUERY]", sanitized)
        sanitized = _WINDOWS_PATH.sub(
            lambda match: self.path_reference(Path(match.group(0))), sanitized
        )
        if len(sanitized) > self._max_text_chars:
            sanitized = f"{sanitized[: self._max_text_chars]}…[TRUNCATED]"
        return sanitized

    def path_reference(self, path: Path) -> str:
        """Return a non-reversible per-process reference instead of a username or filename."""
        digest = hashlib.sha256(self._path_salt + os.fspath(path).encode("utf-8")).hexdigest()
        return f"[PATH:{digest[:16]}]"


class StructuredJsonFormatter(logging.Formatter):
    """Serialize one bounded record; traceback source and local paths are deliberately absent."""

    def __init__(self, redaction: LogRedactionPolicy) -> None:
        super().__init__()
        self._redaction = redaction

    def format(self, record: logging.LogRecord) -> str:
        """Render a stable one-line JSON record with sanitized optional metadata."""
        component = _identifier(getattr(record, "component", record.name), "application")
        event_code = _identifier(getattr(record, "event_code", "UNSPECIFIED"), "UNSPECIFIED")
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "severity": record.levelname,
            "component": component,
            "event_code": event_code,
            "message": self._redaction.redact_text(record.getMessage()),
        }
        for field in ("trace_id", "task_id"):
            raw = getattr(record, field, None)
            if raw is not None:
                payload[field] = _identifier(raw, "INVALID")
        details = getattr(record, "details", None)
        if details is not None:
            payload["details"] = self._redaction.redact(details, key="details")
        if record.exc_info and record.exc_info[1] is not None:
            payload["exception"] = {
                "type": type(record.exc_info[1]).__name__,
                "message": self._redaction.redact_text(str(record.exc_info[1])),
            }
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True, slots=True)
class LoggingRuntime:
    """Installed application logger state that tests and shutdown can close explicitly."""

    logger: logging.Logger
    path: Path
    handler: RotatingFileHandler

    def close(self) -> None:
        """Flush and detach only the handler created by this runtime."""
        self.logger.removeHandler(self.handler)
        self.handler.flush()
        self.handler.close()


def configure_application_logging(
    data_directory: Path,
    build_mode: BuildMode,
    *,
    max_bytes: int = 2 * 1024 * 1024,
    backup_count: int = 5,
    debug_requested: bool = False,
    redaction: LogRedactionPolicy | None = None,
) -> LoggingRuntime:
    """Configure bounded local package logging; production always defaults to INFO."""
    if max_bytes < 64 * 1024 or max_bytes > 32 * 1024 * 1024:
        raise LogConfigurationError("LOG_SIZE_POLICY_INVALID")
    if backup_count < 1 or backup_count > 20:
        raise LogConfigurationError("LOG_RETENTION_POLICY_INVALID")
    data_directory.mkdir(parents=True, exist_ok=True)
    log_directory = data_directory / "logs"
    log_directory.mkdir(exist_ok=True)
    if is_reparse_point(data_directory) or is_reparse_point(log_directory):
        raise LogConfigurationError("LOG_DIRECTORY_UNSAFE")
    log_path = log_directory / "application.jsonl"
    if is_reparse_point(log_path):
        raise LogConfigurationError("LOG_FILE_UNSAFE")
    handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=True,
    )
    handler.setFormatter(StructuredJsonFormatter(redaction or LogRedactionPolicy()))
    logger = logging.getLogger("pc_manager_agent")
    logger.setLevel(
        logging.DEBUG
        if debug_requested and build_mode is not BuildMode.PRODUCTION
        else logging.INFO
    )
    logger.addHandler(handler)
    logger.propagate = False
    return LoggingRuntime(logger=logger, path=log_path, handler=handler)


def _identifier(value: object, fallback: str) -> str:
    rendered = str(value)
    return rendered if _SAFE_IDENTIFIER.fullmatch(rendered) else fallback
