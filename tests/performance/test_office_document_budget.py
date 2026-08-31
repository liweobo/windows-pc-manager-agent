"""Synthetic in-memory Office workloads; never open real user documents or Office apps."""

import io
import time
import tracemalloc

import pytest
from openpyxl import Workbook
from pypdf import PdfWriter

from pc_manager_agent.config.office import OfficeLimits
from pc_manager_agent.domain.office_documents import DocumentFormat
from pc_manager_agent.office.adapters import parse_document


@pytest.mark.performance
@pytest.mark.parametrize("format_", [DocumentFormat.CSV, DocumentFormat.XLSX, DocumentFormat.PDF])
def test_office_bounded_large_synthetic_document(format_):
    if format_ is DocumentFormat.CSV:
        data = ("label,value\n" + "synthetic,123\n" * 10_000).encode()
    elif format_ is DocumentFormat.XLSX:
        book = Workbook()
        for _ in range(10_000):
            book.active.append(["synthetic", 123])
        buffer = io.BytesIO()
        book.save(buffer)
        book.close()
        data = buffer.getvalue()
    else:
        pdf = PdfWriter()
        for _ in range(100):
            pdf.add_blank_page(300, 300)
        buffer = io.BytesIO()
        pdf.write(buffer)
        data = buffer.getvalue()
    tracemalloc.start()
    started = time.monotonic()
    try:
        result = parse_document(data, format_, OfficeLimits())
        seconds = time.monotonic() - started
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    print(f"{format_.value}: {seconds:.3f}s; Python traced peak={peak / 1024**2:.2f}MiB")
    assert peak < 256 * 1024**2
    assert seconds < 30
    assert result.format is format_
