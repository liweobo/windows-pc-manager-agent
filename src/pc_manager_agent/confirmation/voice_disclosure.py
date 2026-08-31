"""Short-lived single-use consent for one outbound voice payload, separate from business consent."""

import hashlib
import json
from collections.abc import Callable
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.voice import FrozenVoiceModel
from pc_manager_agent.persistence.voice import VoiceTranscriptConsumptionStore


class VoiceDisclosure(FrozenVoiceModel):
    """Public exact destination and impact preview; body stays only in the owning service."""

    reference: UUID = Field(default_factory=uuid4)
    owner_ref: UUID
    purpose: Literal["STT", "TTS"]
    destination: str = Field(min_length=1, max_length=300)
    payload_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    quantity: int = Field(ge=0)
    expires_at: float


class VoiceDisclosureService:
    """Never returns a reusable approval flag; approval is consumed immediately before dispatch."""

    def __init__(
        self, store: VoiceTranscriptConsumptionStore, clock: Callable[[], float], ttl: int
    ) -> None:
        self._store, self._clock, self._ttl = store, clock, ttl

    def offer(
        self,
        owner: UUID,
        purpose: Literal["STT", "TTS"],
        destination: str,
        content_digest: str,
        quantity: int,
        options: str = "",
    ) -> VoiceDisclosure:
        """Bind content, destination/model, language/voice options and purpose to one request."""
        digest = self.digest(purpose, destination, content_digest, options)
        value = VoiceDisclosure(
            owner_ref=owner,
            purpose=purpose,
            destination=destination,
            payload_digest=digest,
            quantity=quantity,
            expires_at=self._clock() + self._ttl,
        )
        self._store.offer(value.reference, owner, purpose, digest, value.expires_at)
        return value

    def consume(
        self,
        value: VoiceDisclosure,
        approved: bool,
        destination: str,
        content_digest: str,
        options: str = "",
    ) -> None:
        """Reject any changed body, model, language, provider, expiry or duplicate consent."""
        self._store.consume(
            value.reference,
            value.owner_ref,
            value.purpose,
            self.digest(value.purpose, destination, content_digest, options),
            self._clock(),
            approved=approved,
        )

    @staticmethod
    def digest(purpose: str, destination: str, content: str, options: str) -> str:
        """Canonical digest without storing outbound data."""
        return hashlib.sha256(
            json.dumps(
                [purpose, destination, content, options], ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()
