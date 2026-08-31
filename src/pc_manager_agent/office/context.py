"""Source-addressed chunking, explicit minimal disclosure and deterministic spreadsheet totals."""

from decimal import Decimal, localcontext
from uuid import UUID

from pydantic import Field

from pc_manager_agent.config.office import OfficeLimits
from pc_manager_agent.domain.office_documents import OfficeError, ValueKind
from pc_manager_agent.domain.office_plans import OfficeIntentDraft
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.safety.office.content import require_disclosable
from pc_manager_agent.tools.office_tools.read import OfficeReadResult


class DocumentChunk(FrozenModel):
    """One exact local source span; its ID is provenance, never a path or instruction."""

    chunk_id: str = Field(max_length=250)
    document_id: UUID
    source_reference: str = Field(max_length=100)
    offset: int = Field(ge=0)
    text: str = Field(max_length=2_000)


class OfficeModelRequest(FrozenModel):
    """Exactly what the user sees before external disclosure; no hidden document attachments."""

    user_goal: str = Field(min_length=1, max_length=2_000)
    chunks: tuple[DocumentChunk, ...] = Field(max_length=32)


class OfficeQuote(FrozenModel):
    """An extractive summary item must quote an actual selected source span."""

    chunk_id: str = Field(max_length=250)
    quote: str = Field(min_length=1, max_length=2_000)


class OfficeModelProposal(FrozenModel):
    """Untrusted edit intent or extractive summary, neither grants execution permission."""

    intent: OfficeIntentDraft | None = None
    quotes: tuple[OfficeQuote, ...] = Field(default=(), max_length=16)


class SpreadsheetStatistics(FrozenModel):
    """Exact typed-number aggregate; formulas are explicitly not evaluated."""

    numeric_cells: int
    formula_cells: int
    non_numeric_cells: int
    total: str


class OfficeModelResult(FrozenModel):
    """Validated proposal envelope with content-free provider tracing metadata."""

    proposal: OfficeModelProposal
    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,40}$")
    request_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,100}$")


class DocumentContextBuilder:
    """Expose bounded chunks, and transmit only a user-selected subset under a hard budget."""

    def __init__(self, limits: OfficeLimits) -> None:
        self._limits = limits

    def chunks(self, result: OfficeReadResult) -> tuple[DocumentChunk, ...]:
        """Chunk by paragraph/page/cell and exact offsets; never blend sources or follow links."""
        spans = [(block.reference, block.text) for block in result.document.blocks]
        spans.extend(
            (cell.reference, cell.value.value)
            for sheet in result.document.sheets
            for cell in sheet.cells
        )
        document_id = result.reference.document_id
        chunks: list[DocumentChunk] = []
        for reference, text in spans:
            for offset in range(0, len(text), self._limits.chunk_chars):
                if len(chunks) >= 10_000:
                    raise OfficeError("CONTEXT_CHUNK_LIMIT_SELECT_SMALLER_DOCUMENT")
                chunks.append(
                    DocumentChunk(
                        chunk_id=f"{document_id}:{reference}:{offset}",
                        document_id=document_id,
                        source_reference=reference,
                        offset=offset,
                        text=text[offset : offset + self._limits.chunk_chars],
                    )
                )
        return tuple(chunks)

    def select(
        self, results: tuple[OfficeReadResult, ...], selected: tuple[str, ...], goal: str
    ) -> OfficeModelRequest:
        """Fail on missing/duplicate/oversized selections and known secret patterns."""
        if not selected or len(selected) != len(set(selected)) or len(selected) > 32:
            raise OfficeError("EXPLICIT_CONTEXT_SELECTION_REQUIRED")
        available = {chunk.chunk_id: chunk for result in results for chunk in self.chunks(result)}
        if any(identifier not in available for identifier in selected):
            raise OfficeError("CONTEXT_REFERENCE_UNKNOWN")
        chunks = tuple(available[identifier] for identifier in selected)
        if sum(len(chunk.text) for chunk in chunks) + len(goal) > self._limits.context_chars:
            raise OfficeError("MODEL_CONTEXT_LIMIT")
        for text in (goal, *(chunk.text for chunk in chunks)):
            require_disclosable(text)
        return OfficeModelRequest(user_goal=goal, chunks=chunks)


def validate_proposal(request: OfficeModelRequest, proposal: OfficeModelProposal) -> None:
    """Reject fabricated citations/quotes and intent targets outside the disclosed selection."""
    chunks = {chunk.chunk_id: chunk for chunk in request.chunks}
    for quote in proposal.quotes:
        if quote.chunk_id not in chunks or quote.quote not in chunks[quote.chunk_id].text:
            raise OfficeError("MODEL_SOURCE_QUOTE_MISMATCH")
    intent = proposal.intent
    if intent is not None:
        if not set(intent.document_ids).issubset({chunk.document_id for chunk in request.chunks}):
            raise OfficeError("MODEL_DOCUMENT_SCOPE_EXPANSION")
        if not set(intent.source_references).issubset(chunks):
            raise OfficeError("MODEL_SOURCE_REFERENCE_UNKNOWN")
        allowed_targets = {chunk.source_reference for chunk in request.chunks}
        if any(operation.target not in allowed_targets for operation in intent.operations):
            raise OfficeError("MODEL_EDIT_TARGET_OUTSIDE_CONTEXT")
    if intent is None and not proposal.quotes:
        raise OfficeError("MODEL_EMPTY_PROPOSAL")


def spreadsheet_statistics(result: OfficeReadResult) -> SpreadsheetStatistics:
    """Sum only explicit numeric scalars with Decimal; cached/formula cells never count."""
    total, numeric, formulas, other = Decimal(0), 0, 0, 0
    with localcontext() as context:
        context.prec = 256  # Bounded scalars: 100 significant digits and exponent >= -100.
        for sheet in result.document.sheets:
            for cell in sheet.cells:
                if cell.value.kind is ValueKind.NUMBER:
                    total += Decimal(cell.value.value)
                    numeric += 1
                elif cell.value.kind is ValueKind.FORMULA:
                    formulas += 1
                else:
                    other += 1
    return SpreadsheetStatistics(
        numeric_cells=numeric, formula_cells=formulas, non_numeric_cells=other, total=str(total)
    )
