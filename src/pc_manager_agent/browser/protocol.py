"""Strict JSON-lines protocol between standard-user Main and Browser Worker."""

from __future__ import annotations

from enum import StrEnum
from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.plans import FrozenModel


class BrowserWorkerCommand(StrEnum):
    """Complete worker command allow-list."""

    START = "START"
    NAVIGATE = "NAVIGATE"
    OBSERVE = "OBSERVE"
    PERFORM = "PERFORM"
    DOWNLOAD = "DOWNLOAD"
    CANCEL = "CANCEL"
    CLOSE = "CLOSE"


class BrowserWorkerPayloadType(StrEnum):
    """Closed response payload vocabulary."""

    NONE = "NONE"
    SESSION = "SESSION"
    OBSERVATION = "OBSERVATION"
    ACTION_RESULT = "ACTION_RESULT"
    DOWNLOAD = "DOWNLOAD"


class BrowserWorkerRequest(FrozenModel):
    """One finite request; action data is a validated model JSON string."""

    request_id: UUID = Field(default_factory=uuid4)
    command: BrowserWorkerCommand
    headless: bool | None = None
    url: str | None = Field(default=None, max_length=4_096)
    allow_insecure_http: bool = False
    action_json: str | None = Field(default=None, max_length=20_000)
    temporary_directory: str | None = Field(default=None, max_length=4_096)

    @model_validator(mode="after")
    def enforce_command_shape(self) -> Self:
        """Reject unused fields so the transport cannot become a generic runner."""
        if (self.command is BrowserWorkerCommand.START) != (self.headless is not None):
            raise ValueError("Only START carries headless")
        if (self.command is BrowserWorkerCommand.NAVIGATE) != (self.url is not None):
            raise ValueError("Only NAVIGATE carries URL")
        if self.allow_insecure_http and self.command is not BrowserWorkerCommand.NAVIGATE:
            raise ValueError("Only NAVIGATE can carry insecure HTTP approval")
        action_commands = {BrowserWorkerCommand.PERFORM, BrowserWorkerCommand.DOWNLOAD}
        if (self.command in action_commands) != (self.action_json is not None):
            raise ValueError("Only action commands carry action JSON")
        if (self.command is BrowserWorkerCommand.DOWNLOAD) != (
            self.temporary_directory is not None
        ):
            raise ValueError("Only DOWNLOAD carries a temporary directory")
        return self


class BrowserWorkerResponse(FrozenModel):
    """One bounded response with an explicit typed payload discriminator."""

    request_id: UUID
    success: bool
    reason_code: str = Field(pattern=r"^[A-Z0-9_]+$")
    payload_type: BrowserWorkerPayloadType = BrowserWorkerPayloadType.NONE
    payload_json: str | None = Field(default=None, max_length=200_000)

    @model_validator(mode="after")
    def require_matching_payload(self) -> Self:
        """Prevent ambiguous or untyped response material."""
        if (self.payload_type is BrowserWorkerPayloadType.NONE) != (self.payload_json is None):
            raise ValueError("Worker payload type and JSON must agree")
        return self
