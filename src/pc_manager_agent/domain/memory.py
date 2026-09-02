"""User-controlled, scoped Memory models that never represent authorization."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.agents import AgentRole
from pc_manager_agent.domain.plans import FrozenModel


class MemoryCategory(StrEnum):
    """Finite categories permitted in persistent Memory."""

    USER_PREFERENCE = "USER_PREFERENCE"
    UI_PREFERENCE = "UI_PREFERENCE"
    COMMON_DIRECTORY_REFERENCE = "COMMON_DIRECTORY_REFERENCE"
    COMMON_APPLICATION_REFERENCE = "COMMON_APPLICATION_REFERENCE"
    TASK_PREFERENCE = "TASK_PREFERENCE"
    PROVIDER_PREFERENCE = "PROVIDER_PREFERENCE"
    RECENT_CONTEXT_REFERENCE = "RECENT_CONTEXT_REFERENCE"


class MemoryScope(StrEnum):
    """Read boundaries used for least-context Memory queries."""

    GLOBAL_PREFERENCE = "GLOBAL_PREFERENCE"
    FILE = "FILE"
    OFFICE = "OFFICE"
    VOICE = "VOICE"
    BROWSER = "BROWSER"
    SYSTEM = "SYSTEM"
    SOFTWARE = "SOFTWARE"
    UI = "UI"


class MemorySensitivity(StrEnum):
    """Sensitivity decision made before persistence."""

    LOW = "LOW"
    MODERATE = "MODERATE"
    SENSITIVE = "SENSITIVE"
    PROHIBITED = "PROHIBITED"


class MemorySourceType(StrEnum):
    """Finite source provenance for explainable Memory."""

    USER_EXPLICIT = "USER_EXPLICIT"
    USER_SETTINGS = "USER_SETTINGS"
    MODEL_CANDIDATE = "MODEL_CANDIDATE"
    OBSERVED_BEHAVIOR = "OBSERVED_BEHAVIOR"


class MemoryConfidence(StrEnum):
    """How directly a candidate reflects user intent."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class MemoryWriteDecision(StrEnum):
    """Deterministic persistence decision."""

    ALLOW = "ALLOW"
    REQUIRE_USER_CONFIRMATION = "REQUIRE_USER_CONFIRMATION"
    EPHEMERAL_ONLY = "EPHEMERAL_ONLY"
    BLOCK = "BLOCK"


class MemoryKey(StrEnum):
    """Closed V1 preference vocabulary; arbitrary instructions are not Memory."""

    RESPONSE_LANGUAGE = "response_language"
    LARGE_FILE_THRESHOLD_BYTES = "large_file_threshold_bytes"
    INACTIVE_DAYS = "inactive_days"
    OFFICE_OUTPUT_FORMAT = "office_output_format"
    OFFICE_SAVE_MODE = "office_save_mode"
    VOICE_OUTPUT_MODE = "voice_output_mode"
    UI_DEFAULT_TAB = "ui_default_tab"
    COMMON_DIRECTORY_REF = "common_directory_ref"
    COMMON_APPLICATION_REF = "common_application_ref"
    PROVIDER_NAME = "provider_name"


class MemoryCandidate(FrozenModel):
    """Untrusted proposal; a deterministic policy decides whether it may persist."""

    candidate_id: UUID = Field(default_factory=uuid4)
    category: MemoryCategory
    scope: MemoryScope
    key: MemoryKey
    value: str = Field(min_length=1, max_length=500, repr=False)
    source_type: MemorySourceType
    source_ref: str | None = Field(default=None, max_length=200)
    confidence: MemoryConfidence
    sensitivity: MemorySensitivity
    explicit_user_intent: bool = False
    proposed_ttl_seconds: int | None = Field(default=None, ge=60, le=31_536_000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MemoryEntry(FrozenModel):
    """One versioned stored preference or non-authoritative reference hint."""

    memory_id: UUID = Field(default_factory=uuid4)
    category: MemoryCategory
    scope: MemoryScope
    key: MemoryKey
    value: str = Field(min_length=1, max_length=500, repr=False)
    source_type: MemorySourceType
    source_ref: str | None = Field(default=None, max_length=200)
    confidence: MemoryConfidence
    sensitivity: MemorySensitivity
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def require_valid_lifecycle(self) -> MemoryEntry:
        """Reject impossible timestamps and prohibited stored sensitivity."""
        if self.updated_at < self.created_at:
            raise ValueError("Memory update cannot precede creation")
        if self.expires_at is not None and self.expires_at <= self.updated_at:
            raise ValueError("Memory expiry must follow its update")
        if self.sensitivity in {MemorySensitivity.SENSITIVE, MemorySensitivity.PROHIBITED}:
            raise ValueError("Sensitive and prohibited Memory cannot be stored")
        return self


class MemoryQuery(FrozenModel):
    """Minimal scoped query requested for one Agent and one reason code."""

    agent_role: AgentRole
    scopes: tuple[MemoryScope, ...]
    categories: tuple[MemoryCategory, ...] = ()
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,79}$")
    limit: int = Field(default=8, ge=1, le=20)

    @model_validator(mode="after")
    def require_unique_filters(self) -> MemoryQuery:
        """Reject broad or ambiguous duplicate filters."""
        if not self.scopes or len(self.scopes) != len(set(self.scopes)):
            raise ValueError("Memory query requires unique scopes")
        if len(self.categories) != len(set(self.categories)):
            raise ValueError("Memory query categories must be unique")
        return self


class MemoryContext(FrozenModel):
    """Bounded query result that remains ordinary user-supplied context data."""

    entries: tuple[MemoryEntry, ...]
    scopes: tuple[MemoryScope, ...]
    query_reason: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,79}$")


class MemoryEventAction(StrEnum):
    """Metadata-only Memory journal actions."""

    CREATED = "CREATED"
    UPDATED = "UPDATED"
    DELETED = "DELETED"
    CLEARED_SCOPE = "CLEARED_SCOPE"
    CLEARED_ALL = "CLEARED_ALL"
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    BLOCKED = "BLOCKED"
    EXPIRED = "EXPIRED"


class MemoryEvent(FrozenModel):
    """Value-free local journal event separate from the application audit table."""

    event_id: UUID = Field(default_factory=uuid4)
    memory_id: UUID | None = None
    action: MemoryEventAction
    scope: MemoryScope | None = None
    key_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,79}$")
    affected_count: int = Field(default=0, ge=0)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
