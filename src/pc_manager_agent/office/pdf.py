"""Read-only PDF extraction; no rendering, JavaScript, attachment or link execution."""

import io

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from pc_manager_agent.config.office import OfficeLimits
from pc_manager_agent.domain.office_documents import (
    DocumentBlock,
    DocumentFormat,
    DocumentSupport,
    OfficeError,
    StructuredDocument,
)


def parse_pdf(data: bytes, limits: OfficeLimits) -> StructuredDocument:
    """Extract bounded page text; run inside the resource-limited parser worker."""
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise OfficeError("PASSWORD_PROTECTED_UNSUPPORTED")
        count = len(reader.pages)
        if count > limits.max_pages:
            raise OfficeError("PAGE_LIMIT")
        blocks: list[DocumentBlock] = []
        warnings: set[str] = set()
        total = 0
        root = reader.root_object
        if any(key in root for key in ("/OpenAction", "/AA", "/Names", "/AcroForm")):
            warnings.add("PDF_ACTIVE_CONTENT_IGNORED")
        for index, page in enumerate(reader.pages, 1):
            text = page.extract_text()
            total += len(text)
            if total > limits.max_text_chars:
                raise OfficeError("TEXT_LIMIT")
            if not text.strip():
                warnings.add("OCR_REQUIRED_OR_EMPTY_PAGE")
            blocks.append(DocumentBlock(reference=f"page:{index}", text=text, page=index))
        return StructuredDocument(
            format=DocumentFormat.PDF,
            support=DocumentSupport.READ_ONLY,
            blocks=tuple(blocks),
            page_count=count,
            warnings=tuple(sorted(warnings)),
        )
    except OfficeError:
        raise
    except (PdfReadError, ValueError, OSError, RecursionError) as exc:
        raise OfficeError("MALFORMED_PDF") from exc
