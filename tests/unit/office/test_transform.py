from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from openpyxl import Workbook
from pydantic import ValidationError

from pc_manager_agent.config.office import OfficeLimits
from pc_manager_agent.domain.file_operations import FileObjectKind, FileState
from pc_manager_agent.domain.office_documents import (
    DocumentFormat,
    DocumentReference,
    DocumentSupport,
    OfficeDocumentIdentity,
    OfficeError,
    OfficeValue,
    ValueKind,
    office_digest,
)
from pc_manager_agent.domain.office_plans import (
    DocumentEditPlan,
    DocumentOperation,
    DocumentOperationKind,
    OfficeIntentDraft,
    OutputMode,
    OutputSpecification,
)
from pc_manager_agent.office.adapters import parse_document, serialize_document
from pc_manager_agent.office.transform import (
    apply_operation,
    convert_document,
    document_diff,
    merge_documents,
    transform_documents,
)


def operation(
    kind: DocumentOperationKind,
    target: str,
    value: str,
    value_kind: ValueKind = ValueKind.TEXT,
    **kwargs: object,
) -> DocumentOperation:
    return DocumentOperation.model_validate(
        {
            "kind": kind,
            "target": target,
            "value": {"kind": value_kind, "value": value},
            **kwargs,
        }
    )


def test_text_append_replace_heading_and_diff() -> None:
    document = parse_document(b"old", DocumentFormat.TXT, OfficeLimits())
    updated = apply_operation(
        document, operation(DocumentOperationKind.REPLACE_TEXT, "body", "new")
    )
    assert updated.blocks[0].text == "new"
    differences = document_diff(document, updated)
    assert differences[0].before == "old"
    updated = apply_operation(
        updated, operation(DocumentOperationKind.APPEND_PARAGRAPH, "end", "tail")
    )
    assert updated.blocks[0].text == "new\ntail"
    with pytest.raises(OfficeError):
        apply_operation(updated, operation(DocumentOperationKind.REPLACE_TEXT, "missing", "x"))
    with pytest.raises(OfficeError, match="EXPECTED"):
        apply_operation(
            updated,
            operation(
                DocumentOperationKind.REPLACE_TEXT,
                "body",
                "x",
                expected=OfficeValue(value="wrong").model_dump(),
            ),
        )
    with pytest.raises(OfficeError):
        apply_operation(updated, operation(DocumentOperationKind.APPEND_PARAGRAPH, "body", "x"))
    with pytest.raises(OfficeError):
        apply_operation(updated, operation(DocumentOperationKind.SET_HEADING, "body", "x"))


def test_csv_filter_sort_rename_and_conversion() -> None:
    document = parse_document(b"name,value\nb,2\n,\na,1\n", DocumentFormat.CSV, OfficeLimits())
    filtered = apply_operation(
        document, operation(DocumentOperationKind.FILTER_EMPTY_ROWS, "rows", "")
    )
    assert filtered.sheets[0].rows == 3
    sorted_ = apply_operation(filtered, operation(DocumentOperationKind.SORT_ROWS, "1", ""))
    assert sorted_.sheets[0].cells[2].value.value == "a"
    renamed = apply_operation(
        sorted_, operation(DocumentOperationKind.RENAME_COLUMN, "2", "amount")
    )
    assert renamed.sheets[0].cells[1].value.value == "amount"
    converted = convert_document(renamed, DocumentFormat.XLSX)
    result = parse_document(serialize_document(converted), DocumentFormat.XLSX, OfficeLimits())
    assert result.sheets[0].cells[1].value.value == "amount"


def make_workbook() -> bytes:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet["A1"], sheet["B1"] = "name", "value"
    sheet["A2"], sheet["B2"] = "a", 10
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def test_numeric_scale_and_sheet_operations() -> None:
    document = parse_document(make_workbook(), DocumentFormat.XLSX, OfficeLimits())
    updated = apply_operation(
        document,
        operation(
            DocumentOperationKind.SCALE_NUMBER,
            "s:0:B2",
            "1.05",
            ValueKind.NUMBER,
        ),
    )
    assert updated.sheets[0].cells[-1].value.value == "10.50"
    updated = apply_operation(
        updated, operation(DocumentOperationKind.RENAME_WORKSHEET, "s:0", "Budget")
    )
    updated = apply_operation(
        updated, operation(DocumentOperationKind.ADD_WORKSHEET, "new", "Other")
    )
    assert [sheet.name for sheet in updated.sheets] == ["Budget", "Other"]
    with pytest.raises(OfficeError):
        apply_operation(updated, operation(DocumentOperationKind.ADD_WORKSHEET, "new", "Budget"))
    with pytest.raises(OfficeError, match="NUMERIC"):
        apply_operation(
            document,
            operation(
                DocumentOperationKind.SCALE_NUMBER,
                "s:0:A2",
                "1.05",
                ValueKind.NUMBER,
            ),
        )


def test_formula_merge_and_protection_gates() -> None:
    document = parse_document(make_workbook(), DocumentFormat.XLSX, OfficeLimits())
    formula = apply_operation(
        document,
        operation(
            DocumentOperationKind.SET_SPREADSHEET_CELL,
            "s:0:B2",
            "=SUM(B1:B1)",
            ValueKind.FORMULA,
        ),
    )
    with pytest.raises(OfficeError, match="FORMULA_CELL"):
        apply_operation(
            formula, operation(DocumentOperationKind.SET_SPREADSHEET_CELL, "s:0:B2", "0")
        )
    sheet = document.sheets[0].model_copy(update={"merged_ranges": ("A1:B2",)})
    with pytest.raises(OfficeError, match="MERGED"):
        apply_operation(
            document.model_copy(update={"sheets": (sheet,)}),
            operation(
                DocumentOperationKind.SET_SPREADSHEET_CELL,
                "s:0:B2",
                "0",
            ),
        )
    merged = merge_documents((document, document), DocumentFormat.XLSX, OfficeLimits())
    assert merged.sheets[0].rows == 3
    changed = apply_operation(
        document, operation(DocumentOperationKind.SET_SPREADSHEET_CELL, "s:0:A1", "different")
    )
    with pytest.raises(OfficeError, match="SCHEMA"):
        merge_documents((document, changed), DocumentFormat.XLSX, OfficeLimits())


def test_strict_plan_unknown_code_and_overlapping_edits(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        OfficeIntentDraft.model_validate(
            {
                "intent": "EDIT_DOCUMENT",
                "document_ids": [],
                "python_code": "danger",
            }
        )
    document = parse_document(b"text", DocumentFormat.TXT, OfficeLimits())
    now = datetime.now(UTC)
    initial = DocumentEditPlan(
        output=OutputSpecification(
            mode=OutputMode.CREATE_NEW, destination_grant_id=uuid4(), format=DocumentFormat.TXT
        ),
        initial_document=document,
        created_at=now,
        expires_at=now + timedelta(seconds=60),
        policy_digest=office_digest(OfficeLimits()),
    )
    assert transform_documents((), initial, OfficeLimits()) == document
    state = FileState(
        path=tmp_path / "source.txt",
        kind=FileObjectKind.FILE,
        volume_serial=1,
        file_id="1",
        size_bytes=4,
        created_ns=1,
        modified_ns=1,
        attributes=0,
    )
    reference = DocumentReference(
        grant_id=uuid4(),
        identity=OfficeDocumentIdentity(
            state=state,
            format=DocumentFormat.TXT,
            sha256="0" * 64,
            security_digest="0" * 64,
            hard_links=1,
        ),
    )
    edit = initial.model_copy(
        update={
            "initial_document": None,
            "inputs": (reference,),
            "output": initial.output.model_copy(update={"mode": OutputMode.SAVE_AS}),
            "operations": (
                operation(DocumentOperationKind.REPLACE_TEXT, "body", "first"),
                operation(DocumentOperationKind.REPLACE_TEXT, "body", "second"),
            ),
        }
    )
    with pytest.raises(OfficeError, match="OVERLAPPING"):
        transform_documents((document,), edit, OfficeLimits())
    readonly = document.model_copy(update={"support": DocumentSupport.READ_ONLY})
    with pytest.raises(OfficeError, match="READ_ONLY"):
        transform_documents((readonly,), edit.model_copy(update={"operations": ()}), OfficeLimits())
