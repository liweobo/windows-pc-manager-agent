"""Bounded text, Markdown, CSV and JSON parsing; no file access or dynamic evaluation."""

from __future__ import annotations

import csv
import io
import json
from decimal import Decimal
from typing import cast

from pc_manager_agent.config.office import OfficeLimits
from pc_manager_agent.domain.office_documents import (
    DocumentBlock,
    DocumentCell,
    DocumentFormat,
    DocumentSheet,
    OfficeError,
    OfficeValue,
    StructuredDocument,
    ValueKind,
)
from pc_manager_agent.safety.office.content import safe_spreadsheet_text


def decode_text(data: bytes) -> tuple[str, str, str]:
    """Decode only explicit UTF BOMs or strict UTF-8, without guessed lossy conversion."""
    encoding = "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    try:
        text = data.decode(encoding)
    except UnicodeError as exc:
        raise OfficeError("UNSUPPORTED_TEXT_ENCODING") from exc
    if "\x00" in text:
        raise OfficeError("BINARY_TEXT_REJECTED")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text, encoding, newline


def strict_json(text: str) -> object:
    """Reject duplicate keys, non-finite numbers and excessive nesting."""

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise OfficeError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def invalid_constant(_value: str) -> None:
        raise OfficeError("NONFINITE_JSON_NUMBER")

    try:
        result: object = json.loads(
            text, object_pairs_hook=pairs, parse_constant=invalid_constant, parse_float=Decimal
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise OfficeError("MALFORMED_JSON") from exc
    pending = [(result, 0)]
    count = 0
    while pending:
        value, depth = pending.pop()
        count += 1
        if depth > 64 or count > 200_000:
            raise OfficeError("JSON_STRUCTURE_LIMIT")
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
        elif isinstance(value, Decimal) and (
            not value.is_finite()
            or abs(value) > Decimal("1e100")
            or abs(int(value.as_tuple().exponent)) > 100
        ):
            raise OfficeError("NONFINITE_OR_EXCESSIVE_JSON_NUMBER")
    return result


def parse_text(
    data: bytes, format_: DocumentFormat, limits: OfficeLimits, *, delimiter: str = ","
) -> StructuredDocument:
    """Create a source-addressed text/table view with explicit CSV dialect."""
    text, encoding, newline = decode_text(data)
    if len(text) > limits.max_text_chars:
        raise OfficeError("TEXT_LIMIT")
    if format_ is DocumentFormat.CSV:
        if delimiter not in {",", ";", "\t"}:
            raise OfficeError("UNSUPPORTED_CSV_DELIMITER")
        cells: list[DocumentCell] = []
        rows = 0
        columns = 0
        try:
            for row_number, row in enumerate(
                csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True), 1
            ):
                rows = row_number
                if rows > limits.max_rows or len(cells) + len(row) > limits.max_cells:
                    raise OfficeError("TABLE_LIMIT")
                columns = max(columns, len(row))
                for column, value in enumerate(row, 1):
                    cells.append(
                        DocumentCell(
                            reference=f"s:0:r:{row_number}:c:{column}",
                            row=row_number,
                            column=column,
                            value=OfficeValue(value=value),
                        )
                    )
        except csv.Error as exc:
            raise OfficeError("MALFORMED_CSV") from exc
        return StructuredDocument(
            format=format_,
            encoding=encoding,
            newline=newline,
            delimiter=delimiter,
            sheets=(
                DocumentSheet(
                    reference="s:0",
                    name="CSV",
                    cells=tuple(cells),
                    rows=rows,
                    columns=columns,
                ),
            ),
        )
    if format_ is DocumentFormat.JSON:
        strict_json(text)
    return StructuredDocument(
        format=format_,
        blocks=(DocumentBlock(reference="body", text=text),),
        encoding=encoding,
        newline=newline,
    )


def serialize_text(document: StructuredDocument) -> bytes:
    """Serialize only the structured view; CSV text is safe for spreadsheet import."""
    if document.format is DocumentFormat.CSV:
        stream = io.StringIO(newline="")
        writer = csv.writer(
            stream,
            delimiter=document.delimiter,
            lineterminator=document.newline,
            quoting=csv.QUOTE_ALL,
        )
        sheet = document.sheets[0]
        indexed = {(cell.row, cell.column): cell.value for cell in sheet.cells}
        for row in range(1, sheet.rows + 1):
            values: list[str] = []
            for column in range(1, sheet.columns + 1):
                value = indexed.get((row, column), OfficeValue())
                if value.kind is ValueKind.FORMULA:
                    raise OfficeError("CSV_FORMULA_WRITE_BLOCKED")
                values.append(
                    safe_spreadsheet_text(value.value)
                    if value.kind is ValueKind.TEXT
                    else value.value
                )
            writer.writerow(values)
        text = stream.getvalue()
    else:
        text = "\n".join(block.text for block in document.blocks)
        if document.format is DocumentFormat.JSON:
            strict_json(text)
    try:
        return text.encode(document.encoding)
    except (UnicodeError, LookupError) as exc:
        raise OfficeError("OUTPUT_ENCODING_FAILED") from exc


def json_set(text: str, pointer: str, value: OfficeValue) -> str:
    """Set an existing JSON pointer only; pointer segments never become file paths."""
    root = strict_json(text)
    if not pointer.startswith("/") or pointer == "/":
        raise OfficeError("JSON_POINTER_REQUIRED")
    segments = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    current: object = root
    try:
        for segment in segments[:-1]:
            if isinstance(current, dict):
                current = current[segment]
            elif isinstance(current, list) and segment.isdecimal():
                current = current[int(segment)]
            else:
                raise OfficeError("JSON_TARGET_NOT_FOUND")
        replacement: object = value.value
        if value.kind is ValueKind.NUMBER:
            number = Decimal(value.value)
            replacement = number
        elif value.kind is ValueKind.BOOLEAN:
            replacement = value.value == "true"
        elif value.kind is ValueKind.EMPTY:
            replacement = None
        elif value.kind is not ValueKind.TEXT:
            raise OfficeError("JSON_VALUE_TYPE_UNSUPPORTED")
        last = segments[-1]
        if isinstance(current, dict) and last in current:
            cast(dict[str, object], current)[last] = replacement
        elif isinstance(current, list) and last.isdecimal() and int(last) < len(current):
            current[int(last)] = replacement
        else:
            raise OfficeError("JSON_TARGET_NOT_FOUND")
    except (KeyError, ValueError, IndexError) as exc:
        raise OfficeError("JSON_TARGET_NOT_FOUND") from exc
    return encode_json_exact(root)


def encode_json_exact(value: object, depth: int = 0) -> str:
    """Encode parsed JSON without converting decimal values to binary floating point."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise OfficeError("NONFINITE_JSON_NUMBER")
        return str(value)
    if isinstance(value, dict):
        body = (
            " " * (2 * (depth + 1))
            + json.dumps(key, ensure_ascii=False)
            + ": "
            + encode_json_exact(item, depth + 1)
            for key, item in value.items()
        )
        return "{\n" + ",\n".join(body) + "\n" + " " * (2 * depth) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(encode_json_exact(item, depth + 1) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False, allow_nan=False)
