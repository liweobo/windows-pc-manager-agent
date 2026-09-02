"""Trust-labelled, task-scoped context models for Stage 5D."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.plans import FrozenModel


class ContextTrustLevel(StrEnum):
    """Provenance label; ordering never converts data into an instruction."""

    SYSTEM_TRUSTED = "SYSTEM_TRUSTED"
    USER_SUPPLIED = "USER_SUPPLIED"
    LOCAL_STRUCTURED_DATA = "LOCAL_STRUCTURED_DATA"
    UNTRUSTED_DOCUMENT = "UNTRUSTED_DOCUMENT"
    UNTRUSTED_WEB = "UNTRUSTED_WEB"
    MODEL_GENERATED = "MODEL_GENERATED"


class DataClassification(StrEnum):
    """Content sensitivity independent from provenance trust."""

    PUBLIC = "PUBLIC"
    USER_DATA = "USER_DATA"
    LOCAL_SYSTEM_METADATA = "LOCAL_SYSTEM_METADATA"
    DOCUMENT_CONTENT = "DOCUMENT_CONTENT"
    WEB_CONTENT = "WEB_CONTENT"
    SENSITIVE = "SENSITIVE"
    CREDENTIAL = "CREDENTIAL"
    # This is a classification label, not a credential value.
    SECRET = "SECRET"  # nosec B105


class ContextSourceKind(StrEnum):
    """Finite origins accepted by the context gateway."""

    USER_GOAL = "USER_GOAL"
    SYSTEM_POLICY = "SYSTEM_POLICY"
    STRUCTURED_RESULT = "STRUCTURED_RESULT"
    DOCUMENT_CHUNK = "DOCUMENT_CHUNK"
    WEB_CHUNK = "WEB_CHUNK"
    MODEL_SUMMARY = "MODEL_SUMMARY"
    MEMORY_ENTRY = "MEMORY_ENTRY"
    RECENT_REFERENCE = "RECENT_REFERENCE"


class ContextReference(FrozenModel):
    """Opaque reference to data retained by its owning subsystem."""

    reference_id: str = Field(min_length=1, max_length=200)
    source_kind: ContextSourceKind
    owner_domain: str = Field(min_length=1, max_length=40, pattern=r"^[A-Z_]+$")
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ContextItem(FrozenModel):
    """One bounded item whose content is volatile and omitted from serialization."""

    reference: ContextReference
    content: str = Field(min_length=1, max_length=32_000, exclude=True, repr=False)
    trust_labels: tuple[ContextTrustLevel, ...]
    classifications: tuple[DataClassification, ...]
    source_references: tuple[str, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def require_labels(self) -> ContextItem:
        """Reject unlabeled or duplicate context before it can reach an Agent."""
        if not self.trust_labels or len(self.trust_labels) != len(set(self.trust_labels)):
            raise ValueError("Context requires unique trust labels")
        if not self.classifications or len(self.classifications) != len(set(self.classifications)):
            raise ValueError("Context requires unique data classifications")
        if len(self.source_references) != len(set(self.source_references)):
            raise ValueError("Context source references must be unique")
        return self


class ContextBudget(FrozenModel):
    """Exact upper bounds attached to one context package."""

    max_messages: int = Field(ge=1, le=20)
    max_chars: int = Field(ge=256, le=32_000)
    max_document_chunks: int = Field(ge=0, le=8)
    max_web_chunks: int = Field(ge=0, le=8)
    max_memory_entries: int = Field(ge=0, le=20)
    max_structured_references: int = Field(ge=1, le=100)


class ContextPackage(FrozenModel):
    """Minimum context built for one runtime-assigned Agent role."""

    task_id: UUID
    node_id: UUID
    agent_role: str = Field(pattern=r"^[A-Z_]+$")
    user_goal: str = Field(min_length=1, max_length=4_000, exclude=True, repr=False)
    goal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: tuple[ContextItem, ...] = ()
    redactions_applied: tuple[str, ...] = ()
    budget: ContextBudget
    prompt_version: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def require_budget_compliance(self) -> ContextPackage:
        """Make budget compliance part of the immutable package schema."""
        if len(self.items) > self.budget.max_messages:
            raise ValueError("Context message budget exceeded")
        used_characters = len(self.user_goal) + sum(len(item.content) for item in self.items)
        if used_characters > self.budget.max_chars:
            raise ValueError("Context character budget exceeded")
        document_count = sum(
            item.reference.source_kind is ContextSourceKind.DOCUMENT_CHUNK for item in self.items
        )
        web_count = sum(
            item.reference.source_kind is ContextSourceKind.WEB_CHUNK for item in self.items
        )
        memory_count = sum(
            item.reference.source_kind is ContextSourceKind.MEMORY_ENTRY for item in self.items
        )
        if document_count > self.budget.max_document_chunks:
            raise ValueError("Document context budget exceeded")
        if web_count > self.budget.max_web_chunks:
            raise ValueError("Web context budget exceeded")
        if memory_count > self.budget.max_memory_entries:
            raise ValueError("Memory context budget exceeded")
        references = sum(1 + len(item.source_references) for item in self.items)
        if references > self.budget.max_structured_references:
            raise ValueError("Structured reference budget exceeded")
        return self


class RecentEntityKind(StrEnum):
    """Short-lived references allowed for conversational continuity."""

    FILE_HINT = "FILE_HINT"
    SOFTWARE_HINT = "SOFTWARE_HINT"
    BROWSER_DOWNLOAD_HINT = "BROWSER_DOWNLOAD_HINT"
    BROWSER_PAGE_HINT = "BROWSER_PAGE_HINT"
    DOCUMENT_HINT = "DOCUMENT_HINT"


class RecentEntityReference(FrozenModel):
    """Ephemeral hint that always requires Fresh resolution by its owning domain."""

    reference_id: UUID = Field(default_factory=uuid4)
    conversation_id: UUID
    kind: RecentEntityKind
    owner_domain: str = Field(pattern=r"^[A-Z_]+$")
    identity_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    requires_fresh_resolution: bool = True

    @model_validator(mode="after")
    def require_ephemeral_lifecycle(self) -> RecentEntityReference:
        """Reject non-expiring hints or attempts to turn them into authority."""
        if self.expires_at <= self.created_at:
            raise ValueError("Recent reference expiry must follow creation")
        if not self.requires_fresh_resolution:
            raise ValueError("Recent references always require Fresh resolution")
        return self
