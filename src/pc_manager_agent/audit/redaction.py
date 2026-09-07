"""Recursive redaction for structured audit data."""

from __future__ import annotations

import re

from pydantic import JsonValue

REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "audio",
    "authorization",
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
    "session",
    "token",
    "transcript",
    "webpage_body",
    "webpage_content",
)
_INLINE_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|authorization|cookie|mfa|oauth[_-]?code|password|secret|token)\b"
    r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_BEARER_SECRET = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}")
_KNOWN_TOKEN = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]{12,}|gh[pousr]_[a-z0-9_]{12,}|"
    r"eyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})\b"
)


def is_sensitive_key(key: str) -> bool:  # 判断是否存在敏感的key字符
    """Return whether a key name could contain credentials."""
    normalized = key.casefold().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def redact_text(value: str) -> str:  # 替换敏感的key字符的值
    """Remove obvious inline secret assignments from free text."""
    redacted = _BEARER_SECRET.sub(f"Bearer {REDACTED}", value)
    redacted = _INLINE_SECRET.sub(lambda match: f"{match.group(1)}={REDACTED}", redacted)
    return _KNOWN_TOKEN.sub(REDACTED, redacted)


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
