"""Channel-neutral request hints, with no executable path, tool, risk or approval fields."""

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.voice import FrozenVoiceModel


class RequestChannel(StrEnum):
    """Input provenance is not trust or authority."""

    TEXT = "TEXT"
    VOICE = "VOICE"
    VOICE_EDITED = "VOICE_EDITED"


class RequestDomain(StrEnum):
    """Finite navigation/preparation destinations, never tool names."""

    FILES = "FILES"
    FILE_OPERATIONS = "FILE_OPERATIONS"
    TRASH = "TRASH"
    DIAGNOSTICS = "DIAGNOSTICS"
    OPTIMIZATION = "OPTIMIZATION"
    CLEANUP = "CLEANUP"
    RECYCLE_BIN_EMPTY = "RECYCLE_BIN_EMPTY"
    PROCESS = "PROCESS"
    STARTUP = "STARTUP"
    SERVICE = "SERVICE"
    SOFTWARE = "SOFTWARE"
    OFFICE = "OFFICE"
    BROWSER = "BROWSER"
    CANCEL = "CANCEL"
    STATUS = "STATUS"
    CONFIRMATION = "CONFIRMATION"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"
    BLOCKED = "BLOCKED"


class VoiceInteractionContext(FrozenVoiceModel):
    """Local hints only; changing selection cannot confer old target authority."""

    active_surface: RequestDomain | None = None
    selected_entity_refs: tuple[UUID, ...] = Field(default=(), max_length=20)
    conversation_id: UUID | None = None
    active_transaction_id: UUID | None = None


class UserRequest(FrozenVoiceModel):
    """Volatile source text shared by text and voice preparation; excluded from persistence."""

    request_id: UUID = Field(default_factory=uuid4)
    channel: RequestChannel
    text: str = Field(min_length=1, max_length=4000, exclude=True, repr=False)
    context: VoiceInteractionContext = Field(default_factory=VoiceInteractionContext)


class RequestRoute(FrozenVoiceModel):
    """Navigation decision. The destination domain must establish every execution gate."""

    request_id: UUID
    domain: RequestDomain
    code: str = Field(default="REQUEST_PREPARATION_ONLY", pattern=r"^[A-Z_]+$")
