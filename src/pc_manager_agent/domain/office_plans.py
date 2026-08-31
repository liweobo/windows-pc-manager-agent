"""Strict finite Office edits, previews and non-executable provider intent."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.office_documents import (
    DocumentFormat,
    DocumentReference,
    OfficeValue,
    StructuredDocument,
    office_digest,
)
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel


class OfficeTaskIntent(StrEnum):
    """Semantic intent only; never grants filesystem or execution authority."""

    READ_DOCUMENT = "READ_DOCUMENT"
    SUMMARIZE_DOCUMENT = "SUMMARIZE_DOCUMENT"
    CREATE_DOCUMENT = "CREATE_DOCUMENT"
    EDIT_DOCUMENT = "EDIT_DOCUMENT"
    TRANSFORM_DOCUMENT = "TRANSFORM_DOCUMENT"
    ANALYZE_SPREADSHEET = "ANALYZE_SPREADSHEET"
    EXTRACT_PDF = "EXTRACT_PDF"
    BATCH_DOCUMENT_TRANSFORM = "BATCH_DOCUMENT_TRANSFORM"


class OutputMode(StrEnum):
    """Existing inputs default to a new independently authorized destination."""

    CREATE_NEW = "CREATE_NEW"
    SAVE_AS = "SAVE_AS"
    EDIT_IN_PLACE = "EDIT_IN_PLACE"
    RESTORE = "RESTORE"
    UNDO_CREATED = "UNDO_CREATED"


class DocumentOperationKind(StrEnum):
    """Executable transformations are finite, never Python/VBA/COM expressions."""

    REPLACE_TEXT = "REPLACE_TEXT"
    APPEND_PARAGRAPH = "APPEND_PARAGRAPH"
    SET_HEADING = "SET_HEADING"
    UPDATE_TABLE_CELL = "UPDATE_TABLE_CELL"
    SET_SPREADSHEET_CELL = "SET_SPREADSHEET_CELL"
    ADD_WORKSHEET = "ADD_WORKSHEET"
    RENAME_WORKSHEET = "RENAME_WORKSHEET"
    SCALE_NUMBER = "SCALE_NUMBER"
    FILTER_EMPTY_ROWS = "FILTER_EMPTY_ROWS"
    SORT_ROWS = "SORT_ROWS"
    RENAME_COLUMN = "RENAME_COLUMN"
    SET_JSON_VALUE = "SET_JSON_VALUE"


class DocumentOperation(FrozenModel):
    """One explicit source-addressed edit with exact expected old value."""

    kind: DocumentOperationKind
    target: str = Field(max_length=200)
    value: OfficeValue
    expected: OfficeValue | None = None
    heading_level: int = Field(default=0, ge=0, le=9)


class OfficeIntentDraft(FrozenModel):
    """Untrusted model proposal; no paths, tool names, risk or confirmation fields."""

    intent: OfficeTaskIntent
    document_ids: tuple[UUID, ...] = Field(max_length=20)
    operations: tuple[DocumentOperation, ...] = Field(default=(), max_length=10_000)
    source_references: tuple[str, ...] = Field(default=(), max_length=100)
    explanation: str = Field(default="", max_length=4_000)


class OutputSpecification(FrozenModel):
    """Opaque destination grant resolved by trusted local code."""

    mode: OutputMode = OutputMode.SAVE_AS
    destination_grant_id: UUID
    format: DocumentFormat


class DocumentEditPlan(FrozenModel):
    """Complete immutable semantic edit, independent of GUI and provider."""

    plan_id: UUID = Field(default_factory=uuid4)
    inputs: tuple[DocumentReference, ...] = Field(default=(), max_length=20)
    operations: tuple[DocumentOperation, ...] = Field(default=(), max_length=10_000)
    output: OutputSpecification
    initial_document: StructuredDocument | None = None
    created_at: datetime
    expires_at: datetime
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_valid_mode(self) -> Self:
        """Reject missing inputs, contradictory creation and invalid expiry."""
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Timezone-aware timestamps required")
        if self.expires_at <= self.created_at:
            raise ValueError("Plan expiry must follow creation")
        if self.output.mode is OutputMode.CREATE_NEW:
            if self.inputs or self.initial_document is None:
                raise ValueError("Create requires only explicit initial content")
        elif not self.inputs:
            raise ValueError("Existing-document operation requires a resolved input")
        elif self.initial_document is not None:
            raise ValueError("Existing-document plan cannot inject a replacement document")
        return self

    def canonical_digest(self) -> str:
        """Hash inputs, output, all operations and resource-policy binding."""
        return office_digest(self)


class DocumentDifference(FrozenModel):
    """Local-only value diff; never serialized into ordinary audit."""

    reference: str
    before: str
    after: str
    before_type: str = "text"
    after_type: str = "text"


class DocumentPreview(FrozenModel):
    """Exact bounded preview with content digest and truthful risk/recovery."""

    preview_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    destination_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    differences: tuple[DocumentDifference, ...]
    warnings: tuple[str, ...] = ()
    risk_level: RiskLevel
    rollback_level: RollbackLevel = RollbackLevel.FULL
    source_bytes: int = Field(ge=0)
    changed_cells: int = Field(ge=0)
    changed_formulas: int = Field(ge=0)
    backup_id: UUID | None = None
    backup_digest: str | None = None
    recovery_of: UUID | None = None
    recovery_backup_digest: str | None = None
    created_at: datetime
    expires_at: datetime

    def canonical_digest(self) -> str:
        """Bind displayed diff and every authority-relevant observation."""
        return office_digest(self)
