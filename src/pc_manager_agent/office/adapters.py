"""Finite in-memory format dispatcher with static safety preflight."""

from pc_manager_agent.config.office import OfficeLimits
from pc_manager_agent.domain.office_documents import (
    DocumentFormat,
    DocumentSupport,
    OfficeError,
    StructuredDocument,
)
from pc_manager_agent.office.docx import parse_docx, serialize_docx
from pc_manager_agent.office.pdf import parse_pdf
from pc_manager_agent.office.text import parse_text, serialize_text
from pc_manager_agent.office.xlsx import parse_xlsx, serialize_xlsx
from pc_manager_agent.safety.office.content import inspect_package


def parse_document(
    data: bytes, format_: DocumentFormat, limits: OfficeLimits, delimiter: str = ","
) -> StructuredDocument:
    """Parse one bounded format; callers must authorize and stabilize its input first."""
    text_formats = {DocumentFormat.TXT, DocumentFormat.MARKDOWN, DocumentFormat.JSON}
    packages = {
        DocumentFormat.DOCX,
        DocumentFormat.DOCM,
        DocumentFormat.XLSX,
        DocumentFormat.XLSM,
        DocumentFormat.PPTX,
        DocumentFormat.PPTM,
    }
    maximum = (
        limits.text_bytes
        if format_ in text_formats
        else limits.package_bytes
        if format_ in packages
        else limits.other_bytes
    )
    if len(data) > maximum:
        raise OfficeError("DOCUMENT_SIZE_LIMIT")
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        raise OfficeError("PASSWORD_PROTECTED_OR_LEGACY_UNSUPPORTED")
    if format_ in packages:
        warnings = inspect_package(data, format_, limits)
        if format_ in {DocumentFormat.DOCX, DocumentFormat.DOCM}:
            return parse_docx(data, format_, warnings, limits)
        if format_ in {DocumentFormat.XLSX, DocumentFormat.XLSM}:
            return parse_xlsx(data, format_, warnings, limits)
        return StructuredDocument(
            format=format_,
            support=DocumentSupport.UNSUPPORTED,
            warnings=(*warnings, "PRESENTATION_DEFERRED"),
        )
    if format_ is DocumentFormat.PDF:
        if not data.startswith(b"%PDF-"):
            raise OfficeError("FORMAT_MISMATCH")
        return parse_pdf(data, limits)
    return parse_text(data, format_, limits, delimiter=delimiter)


def serialize_document(document: StructuredDocument, original: bytes | None = None) -> bytes:
    """Serialize only supported editable structures, never activate native Office."""
    if document.support is not DocumentSupport.EDITABLE:
        raise OfficeError("DOCUMENT_READ_ONLY")
    if document.format is DocumentFormat.DOCX:
        return serialize_docx(document, original)
    if document.format is DocumentFormat.XLSX:
        return serialize_xlsx(document, original)
    if document.format in {
        DocumentFormat.TXT,
        DocumentFormat.MARKDOWN,
        DocumentFormat.CSV,
        DocumentFormat.JSON,
    }:
        return serialize_text(document)
    raise OfficeError("OUTPUT_FORMAT_UNSUPPORTED")
