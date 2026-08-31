import pytest

from pc_manager_agent.config.office import OfficeLimits
from pc_manager_agent.domain.office_documents import DocumentFormat, OfficeError
from pc_manager_agent.office.worker import BoundedOfficeParser
from pc_manager_agent.tools.manifest import CancellationToken


def test_fixed_worker_serializes_without_filesystem_paths():
    from pc_manager_agent.domain.office_documents import DocumentBlock, StructuredDocument

    parser = BoundedOfficeParser(OfficeLimits())
    document = StructuredDocument(
        format=DocumentFormat.TXT,
        blocks=(DocumentBlock(reference="body", text="synthetic output"),),
    )
    data = parser.render(document, None, CancellationToken())
    assert data == b"synthetic output"
    assert parser.parse(data, DocumentFormat.TXT, CancellationToken()) == document


def test_real_bounded_parser_returns_data_without_file_or_code_access() -> None:
    parser = BoundedOfficeParser(OfficeLimits())
    result = parser.parse(
        b"untrusted instructions are text", DocumentFormat.TXT, CancellationToken()
    )
    assert result.blocks[0].text == "untrusted instructions are text"


def test_worker_reports_safe_error_and_prestart_cancellation() -> None:
    parser = BoundedOfficeParser(OfficeLimits())
    with pytest.raises(OfficeError, match="MALFORMED_JSON"):
        parser.parse(b"{invalid", DocumentFormat.JSON, CancellationToken())
    token = CancellationToken()
    token.cancel()
    with pytest.raises(OfficeError, match="CANCELLED"):
        parser.parse(b"text", DocumentFormat.TXT, token)
