from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.audit.office_documents import OfficeAudit
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.authorization.office_documents import OfficeGrantKind, OfficePathGrants
from pc_manager_agent.config.office import OfficeLimits
from pc_manager_agent.confirmation.office_documents import DocumentConfirmations
from pc_manager_agent.domain.office_documents import DocumentFormat, OfficeError, StructuredDocument
from pc_manager_agent.office.adapters import parse_document
from pc_manager_agent.orchestration.office_documents import OfficeDocumentService
from pc_manager_agent.persistence.office_documents import OfficeRepository
from pc_manager_agent.platform_support.windows.office_files import WindowsOfficeFiles
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolInputError, UnknownToolError


class InlineParser:
    def parse(
        self,
        data: bytes,
        format_: DocumentFormat,
        cancellation: CancellationToken,
        delimiter: str = ",",
    ) -> StructuredDocument:
        return parse_document(data, format_, OfficeLimits(), delimiter)


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[OfficeDocumentService]:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "protected"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "protected-roaming"))
    repository = OfficeRepository(tmp_path / "office.db")
    audit_repository = AuditRepository(tmp_path / "audit.db")
    audit_repository.initialize()
    audit = OfficeAudit(audit_repository)
    result = OfficeDocumentService(
        OfficePathGrants(lambda: (tmp_path / "forbidden",)),
        WindowsOfficeFiles(),
        InlineParser(),
        DocumentConfirmations(repository, audit),
        audit,
        OfficeLimits(),
    )
    yield result
    repository.close()
    audit_repository.close()


def test_exact_selection_confirmation_read_replay_and_no_content_audit(
    service: OfficeDocumentService,
    tmp_path: Path,
) -> None:
    path = tmp_path / "explicit.txt"
    path.write_text("PRIVATE_DOCUMENT_SENTINEL", encoding="utf-8")
    grant = service.grants.select(path, OfficeGrantKind.READ)
    prepared = service.prepare_read((grant.grant_id,))
    with pytest.raises(OfficeError, match="CONFIRMATION"):
        service.read(prepared.plan.plan_id)
    service.confirm_read(prepared.plan.plan_id, True)
    results = service.read(prepared.plan.plan_id)
    assert len(results) == 1
    assert results[0].document.blocks[0].text == "PRIVATE_DOCUMENT_SENTINEL"
    assert service.result(results[0].reference.document_id) == results[0]
    with pytest.raises(OfficeError, match="PLAN_MISSING"):
        service.read(prepared.plan.plan_id)
    assert b"PRIVATE_DOCUMENT_SENTINEL" not in (tmp_path / "audit.db").read_bytes()
    service.clear_results()
    with pytest.raises(OfficeError):
        service.result(results[0].reference.document_id)


def test_registry_cannot_bypass_confirmed_scope(
    service: OfficeDocumentService, tmp_path: Path
) -> None:
    path = tmp_path / "read.txt"
    path.write_bytes(b"not granted by tool")
    grant = service.grants.select(path, OfficeGrantKind.READ)
    with pytest.raises(OfficeError, match="OUTSIDE_CONFIRMED"):
        service.registry.execute(
            "office.document.read",
            {
                "plan_id": str(uuid4()),
                "grant_id": str(grant.grant_id),
            },
        )
    with pytest.raises(ToolInputError):
        service.registry.execute(
            "office.document.read",
            {
                "plan_id": str(uuid4()),
                "grant_id": str(grant.grant_id),
                "path": str(path),
            },
        )
    for name in ("office.run_macro", "office.run_python", "office.com.call", "office.powershell"):
        with pytest.raises(UnknownToolError):
            service.registry.execute(name, {})


def test_revocation_and_output_grant_cannot_authorize_read(
    service: OfficeDocumentService,
    tmp_path: Path,
) -> None:
    path = tmp_path / "read.txt"
    path.write_bytes(b"test")
    grant = service.grants.select(path, OfficeGrantKind.READ)
    prepared = service.prepare_read((grant.grant_id,))
    service.grants.revoke(grant.grant_id)
    with pytest.raises(OfficeError, match="GRANT_MISSING"):
        service.confirm_read(prepared.plan.plan_id, True)
    output = service.grants.select(tmp_path / "new.txt", OfficeGrantKind.OUTPUT)
    with pytest.raises(OfficeError):
        service.prepare_read((output.grant_id,))


def test_read_cancel_fails_without_reusable_authority(
    service: OfficeDocumentService,
    tmp_path: Path,
) -> None:
    source = tmp_path / "cancel.txt"
    source.write_bytes(b"data")
    grant = service.grants.select(source, OfficeGrantKind.READ)
    prepared = service.prepare_read((grant.grant_id,))
    service.confirm_read(prepared.plan.plan_id, True)
    token = CancellationToken()
    token.cancel()
    with pytest.raises(OfficeError, match="CANCELLED"):
        service.read(prepared.plan.plan_id, token)
    with pytest.raises(OfficeError, match="PLAN_MISSING"):
        service.read(prepared.plan.plan_id)


def test_selection_scope_and_limits(service: OfficeDocumentService, tmp_path: Path) -> None:
    forbidden = tmp_path / "forbidden"
    forbidden.mkdir()
    source = forbidden / "no.txt"
    source.write_bytes(b"private")
    with pytest.raises(OfficeError, match="PATH_BLOCKED"):
        service.grants.select(source, OfficeGrantKind.READ)
    with pytest.raises(OfficeError, match="UNSUPPORTED"):
        service.grants.select(tmp_path / "script.exe", OfficeGrantKind.READ)
    with pytest.raises(OfficeError):
        service.prepare_read(())
    source = tmp_path / "good.txt"
    source.write_bytes(b"ok")
    grant = service.grants.select(source, OfficeGrantKind.READ)
    with pytest.raises(OfficeError):
        service.prepare_read((grant.grant_id, grant.grant_id))
    with pytest.raises(OfficeError):
        service.prepare_read((grant.grant_id,), delimiter="|")
