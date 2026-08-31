"""Deterministic edit, resource, risk and semantic round-trip validation."""

from datetime import datetime
from decimal import Decimal

from pc_manager_agent.config.office import OfficeLimits
from pc_manager_agent.domain.office_documents import (
    DocumentFormat,
    DocumentSupport,
    OfficeError,
    StructuredDocument,
    ValueKind,
    office_digest,
)
from pc_manager_agent.domain.office_plans import DocumentEditPlan, OutputMode
from pc_manager_agent.domain.risk import RiskLevel


def validate_edit_plan(plan: DocumentEditPlan, limits: OfficeLimits, now: datetime) -> None:
    """Reject expired, oversized, duplicate or unsupported output plans before preparation."""
    if plan.policy_digest != office_digest(limits) or not plan.created_at <= now < plan.expires_at:
        raise OfficeError("EDIT_PLAN_CHANGED_OR_EXPIRED")
    if len(plan.inputs) > limits.max_files or len(plan.operations) > limits.max_operations:
        raise OfficeError("EDIT_PLAN_LIMIT")
    if len({item.identity.state.path for item in plan.inputs}) != len(plan.inputs):
        raise OfficeError("DUPLICATE_DOCUMENT_INPUT")
    if plan.output.format not in {
        DocumentFormat.TXT,
        DocumentFormat.MARKDOWN,
        DocumentFormat.JSON,
        DocumentFormat.CSV,
        DocumentFormat.DOCX,
        DocumentFormat.XLSX,
    }:
        raise OfficeError("OUTPUT_FORMAT_UNSUPPORTED")
    if plan.output.mode in {OutputMode.EDIT_IN_PLACE, OutputMode.RESTORE} and len(plan.inputs) != 1:
        raise OfficeError("INPLACE_REQUIRES_ONE_DOCUMENT")


def validate_structure(document: StructuredDocument, limits: OfficeLimits) -> None:
    """Prevent duplicate references, oversized dimensions and ambiguous cell coordinates."""
    if document.support is not DocumentSupport.EDITABLE or not document.complete:
        raise OfficeError("DOCUMENT_READ_ONLY_OR_INCOMPLETE")
    references: list[str] = [block.reference for block in document.blocks]
    cells = dimensions = 0
    for sheet in document.sheets:
        references.append(sheet.reference)
        references.extend(cell.reference for cell in sheet.cells)
        coordinates = {(cell.row, cell.column) for cell in sheet.cells}
        if len(coordinates) != len(sheet.cells) or any(
            row > sheet.rows or column > sheet.columns for row, column in coordinates
        ):
            raise OfficeError("CELL_COORDINATES_AMBIGUOUS")
        if sheet.rows > limits.max_rows:
            raise OfficeError("ROW_LIMIT")
        cells += len(sheet.cells)
        dimensions += sheet.rows * sheet.columns
    if len(references) != len(set(references)):
        raise OfficeError("DOCUMENT_REFERENCES_AMBIGUOUS")
    if cells > limits.max_cells or dimensions > limits.max_cells:
        raise OfficeError("CELL_LIMIT")
    if sum(len(block.text) for block in document.blocks) > limits.max_text_chars:
        raise OfficeError("TEXT_LIMIT")
    if len(document.sheets) > limits.max_sheets:
        raise OfficeError("SHEET_LIMIT")


def require_roundtrip(expected: StructuredDocument, observed: StructuredDocument) -> None:
    """Verify reopened values, types, formula text, headings and table/sheet structure."""
    if observed.support is not DocumentSupport.EDITABLE or not observed.complete:
        raise OfficeError("OUTPUT_UNSUPPORTED_AFTER_WRITE")
    if expected.format != observed.format or expected.blocks != observed.blocks:
        raise OfficeError("OUTPUT_TEXT_VERIFICATION_FAILED")
    if len(expected.sheets) != len(observed.sheets):
        raise OfficeError("OUTPUT_SHEET_VERIFICATION_FAILED")
    for before, after in zip(expected.sheets, observed.sheets, strict=True):
        if (before.name, before.rows, before.columns, before.merged_ranges, before.protected) != (
            after.name,
            after.rows,
            after.columns,
            after.merged_ranges,
            after.protected,
        ):
            raise OfficeError("OUTPUT_STRUCTURE_VERIFICATION_FAILED")
        # XLSX does not serialize an empty cell as a value. No other type is discarded.
        left = {
            (cell.row, cell.column): cell
            for cell in before.cells
            if cell.value.kind is not ValueKind.EMPTY
        }
        right = {
            (cell.row, cell.column): cell
            for cell in after.cells
            if cell.value.kind is not ValueKind.EMPTY
        }
        if left.keys() != right.keys():
            raise OfficeError("OUTPUT_CELL_VERIFICATION_FAILED")
        for address, wanted in left.items():
            actual = right[address]
            if (
                wanted.number_format != actual.number_format
                or wanted.value.kind != actual.value.kind
            ):
                raise OfficeError("OUTPUT_CELL_TYPE_CHANGED")
            if wanted.value.kind is ValueKind.NUMBER:
                equal = Decimal(wanted.value.value) == Decimal(actual.value.value)
            else:
                equal = wanted.value == actual.value
            if not equal:
                raise OfficeError("OUTPUT_CELL_VALUE_CHANGED")


def edit_risk(plan: DocumentEditPlan, changed_cells: int, limits: OfficeLimits) -> RiskLevel:
    """Classify from deterministic impact, never a model-provided risk label."""
    if changed_cells > limits.max_edit_cells:
        raise OfficeError("EDIT_CELL_LIMIT")
    if (
        len(plan.inputs) >= limits.high_impact_files
        or changed_cells >= limits.high_impact_cells
        or sum(item.identity.state.size_bytes for item in plan.inputs) >= limits.high_impact_bytes
    ):
        return RiskLevel.R2_HIGH_IMPACT
    if plan.output.mode in {OutputMode.EDIT_IN_PLACE, OutputMode.RESTORE}:
        return RiskLevel.R2
    return RiskLevel.R1
