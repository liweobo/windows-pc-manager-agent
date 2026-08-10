"""Digest-bound consent for sending minimal data to an external model provider."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from pc_manager_agent.confirmation.models import ConfirmationState


class ExternalDataPurpose(StrEnum):
    """Allowed reasons for external data processing in Stage 1."""

    PLANNING = "planning"
    EXPLANATION = "explanation"


class ExternalDataConsentError(RuntimeError):
    """Raised when external processing lacks exact, current user consent."""


class ExternalDataConsentRequest(BaseModel):
    """Expiry-bound approval for one exact redacted provider payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    confirmation_id: UUID = Field(default_factory=uuid4)
    purpose: ExternalDataPurpose
    provider: str = Field(min_length=1, max_length=80)
    payload_digest: str = Field(min_length=64, max_length=64)
    object_summary: str = Field(min_length=1, max_length=2_000)
    expires_at: datetime
    state: ConfirmationState = ConfirmationState.PENDING


class ExternalDataConsentService:
    """Require exact consent before any Stage 1 provider network call."""

    def __init__(
        self,
        ttl_seconds: int = 300,
        now: Callable[[], datetime] | None = None,
        on_resolved: Callable[[ExternalDataConsentRequest], None] | None = None,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self._requests: dict[UUID, ExternalDataConsentRequest] = {}
        self._on_resolved = on_resolved

    def request(
        self,
        *,
        purpose: ExternalDataPurpose,
        provider: str,
        payload: Mapping[str, JsonValue],
        object_summary: str,
    ) -> ExternalDataConsentRequest:
        """Create an expiring confirmation without retaining the raw payload."""
        request = ExternalDataConsentRequest(
            purpose=purpose,
            provider=provider,
            payload_digest=self.payload_digest(payload),
            object_summary=object_summary,
            expires_at=self._now() + timedelta(seconds=self._ttl_seconds),
        )
        self._requests[request.confirmation_id] = request
        return request

    def resolve(self, confirmation_id: UUID, approved: bool) -> ExternalDataConsentRequest:
        """Resolve one pending consent after checking expiry and identity."""
        try:
            request = self._requests[confirmation_id]
        except KeyError as exc:
            raise ExternalDataConsentError("Unknown external-data confirmation") from exc
        if request.state is not ConfirmationState.PENDING:
            raise ExternalDataConsentError("External-data confirmation was already resolved")
        if self._now() >= request.expires_at:
            self._requests[confirmation_id] = request.model_copy(
                update={"state": ConfirmationState.EXPIRED}
            )
            raise ExternalDataConsentError("External-data confirmation expired")
        state = ConfirmationState.APPROVED if approved else ConfirmationState.REJECTED
        resolved = request.model_copy(update={"state": state})
        self._requests[confirmation_id] = resolved
        if self._on_resolved is not None:
            self._on_resolved(resolved)
        return resolved

    def require_approved(
        self,
        confirmation_id: UUID,
        *,
        purpose: ExternalDataPurpose,
        provider: str,
        payload: Mapping[str, JsonValue],
    ) -> None:
        """Fail unless approval matches the current purpose, provider, and payload."""
        try:
            request = self._requests[confirmation_id]
        except KeyError as exc:
            raise ExternalDataConsentError("External-data confirmation is missing") from exc
        if request.state is not ConfirmationState.APPROVED:
            raise ExternalDataConsentError("External-data processing is not approved")
        if self._now() >= request.expires_at:
            raise ExternalDataConsentError("External-data approval expired")
        if request.purpose is not purpose or request.provider != provider:
            raise ExternalDataConsentError("External-data approval purpose or provider changed")
        if request.payload_digest != self.payload_digest(payload):
            raise ExternalDataConsentError("External-data payload changed after approval")

    @staticmethod
    def payload_digest(payload: Mapping[str, JsonValue]) -> str:
        """Return a stable SHA-256 digest for a JSON-compatible payload."""
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
