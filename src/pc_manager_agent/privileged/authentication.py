"""Standard-cryptography integrity abstraction for Stage 4X1 Mock messages."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Protocol

from pc_manager_agent.domain.privileged_actions import RequestIntegrity


class PrivilegedRequestAuthenticator(Protocol):
    """Authenticate canonical bytes without defining the future Windows trust channel."""

    def sign(self, canonical_message: bytes) -> RequestIntegrity:
        """Return integrity metadata for one canonical message."""
        ...

    def verify(self, canonical_message: bytes, integrity: RequestIntegrity) -> bool:
        """Return whether integrity matches without leaking timing information."""
        ...


class EphemeralHmacAuthenticator:
    """In-process Mock authenticator, not a final cross-privilege trust model."""

    def __init__(self, secret: bytes, *, key_id: str = "stage4x1-ephemeral") -> None:
        if len(secret) < 32:
            raise ValueError("HMAC secret must contain at least 256 bits")
        if not key_id:
            raise ValueError("HMAC key identifier is required")
        self._secret = bytes(secret)
        self._key_id = key_id

    @classmethod
    def generate(cls) -> EphemeralHmacAuthenticator:
        """Create a process-local key that is never persisted or logged."""
        return cls(secrets.token_bytes(32))

    def sign(self, canonical_message: bytes) -> RequestIntegrity:
        """Authenticate canonical bytes with standard HMAC-SHA-256."""
        code = hmac.new(self._secret, canonical_message, hashlib.sha256).hexdigest()
        return RequestIntegrity(key_id=self._key_id, authentication_code=code)

    def verify(self, canonical_message: bytes, integrity: RequestIntegrity) -> bool:
        """Verify key identity and HMAC with a constant-time comparison."""
        if integrity.key_id != self._key_id:
            return False
        expected = hmac.new(self._secret, canonical_message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, integrity.authentication_code)
