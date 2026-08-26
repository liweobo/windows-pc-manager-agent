"""Bounded strict JSON serialization for privileged protocol messages."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from pc_manager_agent.domain.privileged_actions import (
    PROTOCOL_VERSION,
    PrivilegedActionEnvelope,
    PrivilegedActionRequest,
    PrivilegedActionResult,
)


class PrivilegedSerializationError(ValueError):
    """Base failure for malformed or non-canonical protocol input."""


class PrivilegedRequestTooLargeError(PrivilegedSerializationError):
    """Raised before parsing a message that exceeds the configured limit."""


class UnsupportedProtocolVersionError(PrivilegedSerializationError):
    """Raised when the Broker does not implement the exact protocol version."""


class DuplicateJsonKeyError(PrivilegedSerializationError):
    """Raised instead of applying JSON's ambiguous last-value-wins behavior."""


class PrivilegedRequestSerializer:
    """Serialize fixed models and reject ambiguous or oversized input fail closed."""

    def __init__(self, max_request_bytes: int = 32_768) -> None:
        if not 1_024 <= max_request_bytes <= 1_048_576:
            raise ValueError("Privileged request limit must be between 1 KiB and 1 MiB")
        self._max_request_bytes = max_request_bytes

    @property
    def max_request_bytes(self) -> int:
        """Return the byte limit applied before JSON parsing."""
        return self._max_request_bytes

    def canonical_request_bytes(self, request: PrivilegedActionRequest) -> bytes:
        """Return the sole byte representation accepted for digest and authentication."""
        return _canonical_json(request.model_dump(mode="json"))

    def canonical_result_bytes(self, result: PrivilegedActionResult) -> bytes:
        """Return deterministic bytes for result authentication."""
        return _canonical_json(result.model_dump(mode="json"))

    def serialize(self, envelope: PrivilegedActionEnvelope) -> bytes:
        """Serialize one complete envelope after enforcing the transport size limit."""
        value = _canonical_json(envelope.model_dump(mode="json"))
        self._require_size(value)
        return value

    def deserialize(self, serialized: bytes) -> PrivilegedActionEnvelope:
        """Parse one envelope with duplicate-key, version, and strict-schema checks."""
        self._require_size(serialized)
        try:
            decoded = serialized.decode("utf-8", errors="strict")
            raw = json.loads(decoded, object_pairs_hook=_reject_duplicate_keys)
        except (UnicodeDecodeError, json.JSONDecodeError, DuplicateJsonKeyError) as exc:
            raise PrivilegedSerializationError("Malformed privileged request JSON") from exc
        if not isinstance(raw, dict):
            raise PrivilegedSerializationError("Privileged envelope must be a JSON object")
        request = raw.get("request")
        version = request.get("protocol_version") if isinstance(request, dict) else None
        if version != PROTOCOL_VERSION:
            raise UnsupportedProtocolVersionError("Unsupported privileged protocol version")
        try:
            return PrivilegedActionEnvelope.model_validate(raw)
        except ValidationError as exc:
            raise PrivilegedSerializationError("Privileged request schema is invalid") from exc

    def _require_size(self, serialized: bytes) -> None:
        if len(serialized) > self._max_request_bytes:
            raise PrivilegedRequestTooLargeError("Privileged request exceeds byte limit")


def _canonical_json(value: object) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise PrivilegedSerializationError("Value is not canonical JSON") from exc
    return text.encode("utf-8")


def _reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJsonKeyError(f"Duplicate JSON key: {key}")
        value[key] = item
    return value
