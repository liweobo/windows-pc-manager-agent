"""Provider-neutral Office identities and bounded structured content."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.file_operations import FileState
from pc_manager_agent.domain.plans import FrozenModel


class OfficeError(RuntimeError):
    """A stable code safe for audit; never includes document content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def office_digest(model: FrozenModel) -> str:
    """Hash a canonical validated model without logging its contents."""
    import json

    payload = json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class DocumentFormat(StrEnum):
    """Finite file formats; macro formats are inspection-only."""

    TXT = "txt"
    MARKDOWN = "md"
    CSV = "csv"
    JSON = "json"
    DOCX = "docx"
    XLSX = "xlsx"
    PDF = "pdf"
    DOCM = "docm"
    XLSM = "xlsm"
    PPTX = "pptx"
    PPTM = "pptm"


class DocumentSupport(StrEnum):
    """Truthful adapter capability for this particular input."""

    EDITABLE = "EDITABLE"
    READ_ONLY = "READ_ONLY"
    UNSUPPORTED = "UNSUPPORTED"


class OfficeDocumentIdentity(FrozenModel):
    """Stable OS identity plus full content hash; filenames alone are not identity."""

    state: FileState
    format: DocumentFormat
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    security_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    hard_links: int = Field(ge=1)
    cloud_sync: bool = False
    readonly: bool = False

    def matches(self, other: OfficeDocumentIdentity) -> bool:
        """Compare every execution-relevant observation, excluding access time."""
        return self == other


class DocumentReference(FrozenModel):
    """A local reference resolved only under a current explicit grant."""

    document_id: UUID = Field(default_factory=uuid4)
    grant_id: UUID
    identity: OfficeDocumentIdentity


class ValueKind(StrEnum):
    """Preserved spreadsheet types; formulas are not ordinary text."""

    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    DATETIME = "datetime"
    BOOLEAN = "boolean"
    FORMULA = "formula"
    EMPTY = "empty"


class OfficeValue(FrozenModel):
    """Lossless tagged scalar with bounded serialization."""

    kind: ValueKind = ValueKind.TEXT
    value: str = Field(default="", max_length=100_000)

    @model_validator(mode="after")
    def validate_value(self) -> Self:
        """Reject ambiguous numbers, malformed dates, booleans and empty values."""
        if self.kind is ValueKind.NUMBER:
            try:
                number = Decimal(self.value)
            except InvalidOperation as exc:
                raise ValueError("Invalid numeric scalar") from exc
            if (
                not number.is_finite()
                or abs(number) > Decimal("1e100")
                or len(number.as_tuple().digits) > 100
                or abs(int(number.as_tuple().exponent)) > 100
            ):
                raise ValueError("Numeric scalar is non-finite or too large")
        elif self.kind is ValueKind.DATE:
            date.fromisoformat(self.value)
        elif self.kind is ValueKind.DATETIME:
            datetime.fromisoformat(self.value)
        elif self.kind is ValueKind.BOOLEAN and self.value not in {"true", "false"}:
            raise ValueError("Boolean must be true or false")
        elif self.kind is ValueKind.EMPTY and self.value:
            raise ValueError("Empty scalar cannot contain text")
        elif self.kind is ValueKind.FORMULA and not self.value.startswith("="):
            raise ValueError("Formula requires an equals prefix")
        return self


class DocumentBlock(FrozenModel):
    """One source-addressable paragraph, JSON/text body or PDF page."""

    reference: str = Field(min_length=1, max_length=100)
    text: str = Field(max_length=2_000_000)
    heading_level: int = Field(default=0, ge=0, le=9)
    page: int | None = Field(default=None, ge=1)


class DocumentCell(FrozenModel):
    """One source-addressable typed cell."""

    reference: str = Field(min_length=1, max_length=100)
    row: int = Field(ge=1, le=1_048_576)
    column: int = Field(ge=1, le=16_384)
    value: OfficeValue
    number_format: str = Field(default="General", max_length=500)


class DocumentSheet(FrozenModel):
    """Bounded worksheet/table content; names remain untrusted data."""

    reference: str = Field(min_length=1, max_length=100)
    name: str = Field(max_length=200)
    cells: tuple[DocumentCell, ...] = Field(default=(), max_length=200_000)
    rows: int = Field(default=0, ge=0, le=1_048_576)
    columns: int = Field(default=0, ge=0, le=16_384)
    merged_ranges: tuple[str, ...] = ()
    protected: bool = False


class StructuredDocument(FrozenModel):
    """Safe data transfer object; no library object or executable callback."""

    format: DocumentFormat
    support: DocumentSupport = DocumentSupport.EDITABLE
    blocks: tuple[DocumentBlock, ...] = Field(default=(), max_length=100_000)
    sheets: tuple[DocumentSheet, ...] = Field(default=(), max_length=32)
    warnings: tuple[str, ...] = ()
    encoding: str = "utf-8"
    newline: str = "\n"
    delimiter: str = ","
    formula_count: int = Field(default=0, ge=0)
    page_count: int = Field(default=0, ge=0)
    complete: bool = True

    def content_digest(self) -> str:
        """Bind the complete structured view, including warnings and support."""
        return office_digest(self)
