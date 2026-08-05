"""Recursive redaction for structured audit data."""

from __future__ import annotations

import re

from pydantic import JsonValue

REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "session",
    "token",
)
_INLINE_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|authorization|cookie|password|secret|token)\b\s*[:=]\s*[^\s,;]+"
)


def is_sensitive_key(key: str) -> bool:
    """Return whether a key name could contain credentials."""
    normalized = key.casefold().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def redact_text(value: str) -> str:
    """Remove obvious inline secret assignments from free text."""
    return _INLINE_SECRET.sub(lambda match: f"{match.group(1)}={REDACTED}", value)


def redact_json(value: JsonValue) -> JsonValue:
    """Redact sensitive fields recursively while preserving JSON shape."""
    if isinstance(value, dict):
        return {
            key: REDACTED if is_sensitive_key(key) else redact_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
