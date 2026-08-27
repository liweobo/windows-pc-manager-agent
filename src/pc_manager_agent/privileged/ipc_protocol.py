"""Bounded authenticated framing for the Stage 4X2 local Broker pipe."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import struct
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar
from uuid import UUID

from pydantic import BaseModel, ValidationError

from pc_manager_agent.domain.elevated_broker import (
    BROKER_TRANSPORT_VERSION,
    IpcFrame,
    IpcMessageType,
    canonical_broker_bytes,
    canonical_broker_digest,
)
from pc_manager_agent.domain.privileged_actions import RequestIntegrity

_LENGTH_PREFIX = struct.Struct("<I")
_DEFAULT_MAX_FRAME_BYTES = 65_536
_MAX_MESSAGES = 8
TModel = TypeVar("TModel", bound=BaseModel)
JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class BrokerIpcError(RuntimeError):
    """Base failure for a rejected or unavailable Broker transport."""


class BrokerIpcProtocolError(BrokerIpcError):
    """Raised for malformed, out-of-order, oversized, or downgraded frames."""


class BrokerIpcAuthenticationError(BrokerIpcError):
    """Raised when a session proof, frame MAC, or digest is invalid."""


class BrokerIpcTimeoutError(BrokerIpcError):
    """Raised when one bounded transport operation reaches its deadline."""


class BrokerIpcDisconnectedError(BrokerIpcError):
    """Raised when the peer closes before one complete frame is available."""


class PipeByteStream(Protocol):
    """Minimal byte transport implemented by the Windows named-pipe adapter."""

    def read_exact(self, size: int, *, timeout_seconds: float) -> bytes:
        """Read exactly ``size`` bytes or fail without returning partial data."""
        ...

    def write_all(self, data: bytes, *, timeout_seconds: float) -> None:
        """Write all bytes or fail without claiming a complete frame."""
        ...


class IpcSessionAuthenticator:
    """One-connection HMAC-SHA256 authenticator with constant-time verification."""

    def __init__(self, secret: bytes, *, key_id: str) -> None:
        if len(secret) < 32:
            raise ValueError("IPC session key must contain at least 256 bits")
        if not key_id or len(key_id) > 64:
            raise ValueError("IPC session key ID is invalid")
        self._secret = bytes(secret)
        self._key_id = key_id

    @classmethod
    def generate(cls, *, key_id: str) -> IpcSessionAuthenticator:
        """Create a non-persistent random session authenticator."""
        return cls(secrets.token_bytes(32), key_id=key_id)

    @property
    def secret(self) -> bytes:
        """Return a defensive key copy solely for the authenticated session grant."""
        return bytes(self._secret)

    def sign(self, message: bytes) -> RequestIntegrity:
        """Return HMAC-SHA256 metadata for canonical frame or result bytes."""
        code = hmac.new(self._secret, message, hashlib.sha256).hexdigest()
        return RequestIntegrity(key_id=self._key_id, authentication_code=code)

    def verify(self, message: bytes, integrity: RequestIntegrity) -> bool:
        """Verify key identity and authentication code in constant time."""
        if integrity.key_id != self._key_id:
            return False
        expected = hmac.new(self._secret, message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, integrity.authentication_code)

    def client_proof(self, transcript_digest: str) -> str:
        """Authenticate the complete OS-identity and challenge transcript."""
        return hmac.new(
            self._secret,
            f"stage4x2-client-proof:{transcript_digest}".encode("ascii"),
            hashlib.sha256,
        ).hexdigest()


class BrokerFrameCodec:
    """Encode and strictly parse one length-prefixed canonical JSON frame."""

    def __init__(self, max_frame_bytes: int = _DEFAULT_MAX_FRAME_BYTES) -> None:
        if not 1_024 <= max_frame_bytes <= 1_048_576:
            raise ValueError("Broker frame size must be between 1 KiB and 1 MiB")
        self._maximum = max_frame_bytes

    @property
    def max_frame_bytes(self) -> int:
        """Return the enforced payload limit excluding the four-byte prefix."""
        return self._maximum

    def encode(self, frame: IpcFrame) -> bytes:
        """Encode one validated frame with a little-endian unsigned length prefix."""
        payload = canonical_broker_bytes(frame.model_dump(mode="json"))
        if len(payload) > self._maximum:
            raise BrokerIpcProtocolError("Broker IPC frame exceeds the configured limit")
        return _LENGTH_PREFIX.pack(len(payload)) + payload

    def decode_payload(self, payload: bytes) -> IpcFrame:
        """Reject duplicate keys, invalid UTF-8, unknown fields, and wrong versions."""
        if not payload or len(payload) > self._maximum:
            raise BrokerIpcProtocolError("Broker IPC payload size is invalid")
        try:
            raw = json.loads(
                payload.decode("utf-8", errors="strict"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
            )
            if not isinstance(raw, dict):
                raise BrokerIpcProtocolError("Broker IPC frame must be a JSON object")
            if raw.get("protocol_version") != BROKER_TRANSPORT_VERSION:
                raise BrokerIpcProtocolError("Broker IPC protocol version is unsupported")
            return IpcFrame.model_validate(raw)
        except BrokerIpcProtocolError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise BrokerIpcProtocolError("Broker IPC frame is malformed") from exc

    def decode_prefixed(self, data: bytes) -> IpcFrame:
        """Decode exactly one complete prefixed frame and reject trailing bytes."""
        if len(data) < _LENGTH_PREFIX.size:
            raise BrokerIpcProtocolError("Broker IPC length prefix is truncated")
        (size,) = _LENGTH_PREFIX.unpack(data[: _LENGTH_PREFIX.size])
        if size == 0 or size > self._maximum:
            raise BrokerIpcProtocolError("Broker IPC length prefix is invalid")
        payload = data[_LENGTH_PREFIX.size :]
        if len(payload) != size:
            raise BrokerIpcProtocolError("Broker IPC frame length differs from its prefix")
        return self.decode_payload(payload)

    def receive(self, stream: PipeByteStream, *, timeout_seconds: float) -> IpcFrame:
        """Read exactly one bounded frame from a connected pipe stream."""
        prefix = stream.read_exact(_LENGTH_PREFIX.size, timeout_seconds=timeout_seconds)
        if len(prefix) != _LENGTH_PREFIX.size:
            raise BrokerIpcDisconnectedError("Broker IPC disconnected during length prefix")
        (size,) = _LENGTH_PREFIX.unpack(prefix)
        if size == 0 or size > self._maximum:
            raise BrokerIpcProtocolError("Broker IPC declared frame size is invalid")
        payload = stream.read_exact(size, timeout_seconds=timeout_seconds)
        return self.decode_payload(payload)

    def send(
        self,
        stream: PipeByteStream,
        frame: IpcFrame,
        *,
        timeout_seconds: float,
    ) -> None:
        """Write exactly one complete bounded frame."""
        stream.write_all(self.encode(frame), timeout_seconds=timeout_seconds)


class BrokerMessageSequence:
    """Enforce one fixed message type and sequence number at each protocol step."""

    def __init__(self) -> None:
        self._next = 0

    def require(self, frame: IpcFrame, message_type: IpcMessageType) -> None:
        """Advance only when both message type and monotonically increasing index match."""
        if self._next >= _MAX_MESSAGES:
            raise BrokerIpcProtocolError("Broker IPC message count limit was exceeded")
        if frame.sequence != self._next or frame.message_type is not message_type:
            raise BrokerIpcProtocolError("Broker IPC message sequence is invalid")
        self._next += 1


def build_plain_frame(
    *,
    message_type: IpcMessageType,
    broker_instance_id: UUID,
    request_id: UUID,
    sequence: int,
    payload: BaseModel | dict[str, JsonValue],
    now: Callable[[], datetime] | None = None,
) -> IpcFrame:
    """Build one pre-session frame whose future transcript supplies authentication."""
    raw_payload = _payload_dict(payload)
    return IpcFrame(
        message_type=message_type,
        broker_instance_id=broker_instance_id,
        request_id=request_id,
        sequence=sequence,
        sent_at=(now or (lambda: datetime.now(UTC)))(),
        payload=raw_payload,
        payload_digest=canonical_broker_digest(raw_payload),
    )


def build_authenticated_frame(
    *,
    message_type: IpcMessageType,
    broker_instance_id: UUID,
    request_id: UUID,
    sequence: int,
    payload: BaseModel | dict[str, JsonValue],
    authenticator: IpcSessionAuthenticator,
    now: Callable[[], datetime] | None = None,
) -> IpcFrame:
    """Build a frame whose HMAC covers routing metadata and the exact payload digest."""
    raw_payload = _payload_dict(payload)
    sent_at = (now or (lambda: datetime.now(UTC)))()
    digest = canonical_broker_digest(raw_payload)
    # Pydantic canonicalizes UTC as ``Z``. Sign the exact model serialization so an
    # equivalent ``+00:00`` input cannot create a different authenticated byte string.
    unsigned = IpcFrame.model_construct(
        protocol_version=BROKER_TRANSPORT_VERSION,
        message_type=message_type,
        broker_instance_id=broker_instance_id,
        request_id=request_id,
        sequence=sequence,
        sent_at=sent_at,
        payload=raw_payload,
        payload_digest=digest,
        integrity=None,
    )
    return IpcFrame(
        message_type=message_type,
        broker_instance_id=broker_instance_id,
        request_id=request_id,
        sequence=sequence,
        sent_at=sent_at,
        payload=raw_payload,
        payload_digest=digest,
        integrity=authenticator.sign(unsigned.unsigned_bytes()),
    )


def verify_authenticated_frame(
    frame: IpcFrame,
    authenticator: IpcSessionAuthenticator,
) -> None:
    """Raise unless the frame has a valid session HMAC and unchanged payload digest."""
    if frame.integrity is None or not authenticator.verify(frame.unsigned_bytes(), frame.integrity):
        raise BrokerIpcAuthenticationError("Broker IPC frame authentication failed")


def parse_payload(frame: IpcFrame, model: type[TModel]) -> TModel:
    """Validate a frame payload as one exact Pydantic handshake or result model."""
    try:
        return model.model_validate(frame.payload)
    except ValidationError as exc:
        raise BrokerIpcProtocolError("Broker IPC message payload is invalid") from exc


def transcript_digest(*messages: BaseModel) -> str:
    """Bind the complete ready/hello/challenge exchange to the session proof."""
    return canonical_broker_digest([item.model_dump(mode="json") for item in messages])


def new_opaque_id() -> str:
    """Return a 256-bit URL-safe identifier with no padding."""
    return secrets.token_urlsafe(32)


def _payload_dict(value: BaseModel | dict[str, JsonValue]) -> dict[str, JsonValue]:
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise BrokerIpcProtocolError(f"Duplicate JSON key is forbidden: {key}")
        value[key] = item
    return value


def _reject_non_finite(value: str) -> None:
    raise BrokerIpcProtocolError(f"Non-finite JSON number is forbidden: {value}")
