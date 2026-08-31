from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pc_manager_agent.audit.office_documents import OfficeAudit
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.authorization.office_documents import OfficeGrantKind, OfficePathGrants
from pc_manager_agent.backup.office_documents import DocumentBackupService
from pc_manager_agent.config.office import OfficeLimits
from pc_manager_agent.confirmation.office_documents import DocumentConfirmations
from pc_manager_agent.domain.office_documents import (
    DocumentBlock,
    DocumentFormat,
    OfficeError,
    OfficeValue,
    StructuredDocument,
    office_digest,
)
from pc_manager_agent.domain.office_plans import (
    DocumentEditPlan,
    DocumentOperation,
    DocumentOperationKind,
    OutputMode,
    OutputSpecification,
)
from pc_manager_agent.domain.office_transactions import OfficeTransactionState
from pc_manager_agent.office.adapters import parse_document, serialize_document
from pc_manager_agent.orchestration.office_documents import OfficeDocumentService
from pc_manager_agent.orchestration.office_edits import OfficeEditService
from pc_manager_agent.persistence.office_documents import OfficeRepository
from pc_manager_agent.platform_support.windows.office_files import WindowsOfficeFiles
from pc_manager_agent.platform_support.windows.office_protection import WindowsOfficeDataProtector
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import WriteAuthorizationError


class InlineCodec:
    def parse(
        self,
        data: bytes,
        format_: DocumentFormat,
        cancellation: CancellationToken,
        delimiter: str = ",",
    ) -> StructuredDocument:
        return parse_document(data, format_, OfficeLimits(), delimiter)

    def render(
        self, document: StructuredDocument, original: bytes | None, cancellation: CancellationToken
    ) -> bytes:
        return serialize_document(document, original)


@dataclass
class Harness:
    edits: OfficeEditService
    reads: OfficeDocumentService
    repository: OfficeRepository
    backups: DocumentBackupService
    codec: InlineCodec

    def read(self, path: Path):
        grant = self.reads.grants.select(path, OfficeGrantKind.READ)
        prepared = self.reads.prepare_read((grant.grant_id,))
        self.reads.confirm_read(prepared.plan.plan_id, True)
        return self.reads.read(prepared.plan.plan_id)[0]

    def plan(self, source: Path | None, output: Path, mode: OutputMode = OutputMode.SAVE_AS):
        inputs = (self.read(source).reference,) if source is not None else ()
        grant = self.reads.grants.select(output, OfficeGrantKind.OUTPUT)
        now = datetime.now(UTC)
        return DocumentEditPlan(
            inputs=inputs,
            output=OutputSpecification(
                mode=mode, destination_grant_id=grant.grant_id, format=DocumentFormat.TXT
            ),
            operations=(
                DocumentOperation(
                    kind=DocumentOperationKind.REPLACE_TEXT,
                    target="body",
                    value=OfficeValue(value="changed"),
                ),
            )
            if inputs
            else (),
            initial_document=None
            if inputs
            else StructuredDocument(
                format=DocumentFormat.TXT, blocks=(DocumentBlock(reference="body", text="created"),)
            ),
            created_at=now,
            expires_at=now + timedelta(minutes=5),
            policy_digest=office_digest(OfficeLimits()),
        )

    def approve(self, identifier, *, inplace=False):
        if inplace:
            self.edits.request_backup(identifier)
            self.edits.confirm_backup(identifier, True)
        self.edits.request_plan_confirmation(identifier)
        self.edits.confirm_plan(identifier, True)
        if inplace:
            self.edits.request_immediate_confirmation(identifier)
            self.edits.confirm_immediate(identifier, True)


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Harness]:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "protected"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "protected-roaming"))
    repository = OfficeRepository(tmp_path / "office.db")
    audit_repository = AuditRepository(tmp_path / "audit.db")
    audit_repository.initialize()
    limits, files, codec = OfficeLimits(), WindowsOfficeFiles(), InlineCodec()
    audit = OfficeAudit(audit_repository)
    confirmations = DocumentConfirmations(repository, audit)
    reads = OfficeDocumentService(
        OfficePathGrants(lambda: ()), files, codec, confirmations, audit, limits
    )
    backups = DocumentBackupService(
        tmp_path / "backups", repository, files, WindowsOfficeDataProtector(), limits
    )
    edits = OfficeEditService(
        reads,
        files,
        codec,
        repository,
        backups,
        confirmations,
        audit,
        limits,
        is_elevated=lambda: False,
    )
    yield Harness(edits, reads, repository, backups, codec)
    repository.close()
    audit_repository.close()


@pytest.mark.parametrize("mode", [OutputMode.CREATE_NEW, OutputMode.SAVE_AS])
def test_verified_creation_and_save_as(harness: Harness, tmp_path: Path, mode: OutputMode):
    source = tmp_path / "source.txt"
    source.write_bytes(b"original")
    output = tmp_path / "output.txt"
    plan = harness.plan(source if mode is OutputMode.SAVE_AS else None, output, mode)
    preview = harness.edits.prepare(plan)
    assert not output.exists()
    with pytest.raises(OfficeError, match="CONFIRMATION"):
        harness.edits.execute(preview.transaction_id)
    harness.approve(preview.transaction_id)
    result = harness.edits.execute(preview.transaction_id)
    assert result.state is OfficeTransactionState.COMPLETED
    assert result.result is not None
    assert result.confirmation_ids
    events = harness.edits._audit._repository.list_recent()
    finished = next(item for item in events if item.event_type == "office.write.finished")
    assert finished.duration_ms is not None
    assert finished.tool_name == "office.document.create"
    assert finished.plan_id == str(plan.plan_id)
    assert "created" not in str(finished.parameters)
    assert "changed" not in str(finished.parameters)
    assert "output_identity_digest" in finished.parameters
    assert output.read_bytes() == (b"changed" if mode is OutputMode.SAVE_AS else b"created")
    assert source.read_bytes() == b"original"
    with pytest.raises(OfficeError):
        harness.edits.execute(preview.transaction_id)


def test_inplace_requires_verified_backup_and_two_confirmations(harness: Harness, tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_bytes(b"original")
    preview = harness.edits.prepare(harness.plan(source, source, OutputMode.EDIT_IN_PLACE))
    with pytest.raises(OfficeError, match="BACKUP_REQUIRED"):
        harness.edits.request_plan_confirmation(preview.transaction_id)
    harness.edits.request_backup(preview.transaction_id)
    backed = harness.edits.confirm_backup(preview.transaction_id, True)
    assert backed.backup_id and backed.preview_id != preview.preview_id
    assert harness.backups.restore_bytes(backed.backup_id, CancellationToken()) == b"original"
    harness.edits.request_plan_confirmation(preview.transaction_id)
    harness.edits.confirm_plan(preview.transaction_id, True)
    with pytest.raises(OfficeError, match="IMMEDIATE_CONFIRMATION"):
        harness.edits.execute(preview.transaction_id)
    harness.edits.request_immediate_confirmation(preview.transaction_id)
    harness.edits.confirm_immediate(preview.transaction_id, True)
    result = harness.edits.execute(preview.transaction_id)
    assert result.state is OfficeTransactionState.COMPLETED
    assert source.read_bytes() == b"changed"
    assert result.retained_original_path.read_bytes() == b"original"


def test_restore_is_independent_and_does_not_overwrite_newer_content(
    harness: Harness, tmp_path: Path
):
    source = tmp_path / "source.txt"
    source.write_bytes(b"original")
    preview = harness.edits.prepare(harness.plan(source, source, OutputMode.EDIT_IN_PLACE))
    harness.approve(preview.transaction_id, inplace=True)
    edited = harness.edits.execute(preview.transaction_id)
    current = harness.read(source)
    grant = harness.reads.grants.select(source, OfficeGrantKind.OUTPUT)
    restore = harness.edits.prepare_restore(
        edited.transaction_id, current.reference.document_id, grant.grant_id
    )
    harness.approve(restore.transaction_id, inplace=True)
    restored = harness.edits.execute(restore.transaction_id)
    assert restored.state is OfficeTransactionState.COMPLETED
    assert source.read_bytes() == b"original"
    assert restored.retained_original_path.read_bytes() == b"changed"
    assert (
        harness.repository.transaction(edited.transaction_id).state
        is OfficeTransactionState.RESTORED
    )
    source.write_bytes(b"newer user version")
    current = harness.read(source)
    with pytest.raises(OfficeError, match="RESTORE_CONFLICT"):
        harness.edits.prepare_restore(
            restored.transaction_id, current.reference.document_id, grant.grant_id
        )
    assert source.read_bytes() == b"newer user version"


def test_changed_source_and_same_path_replacement_invalidate_authority(
    harness: Harness, tmp_path: Path
):
    source = tmp_path / "source.txt"
    source.write_bytes(b"original")
    preview = harness.edits.prepare(harness.plan(source, tmp_path / "out.txt"))
    harness.approve(preview.transaction_id)
    source.rename(tmp_path / "retained.txt")
    source.write_bytes(b"original")
    with pytest.raises(OfficeError, match="IDENTITY_CHANGED"):
        harness.edits.execute(preview.transaction_id)
    assert not (tmp_path / "out.txt").exists()


def test_conflict_registry_bypass_and_cancel_are_safe(harness: Harness, tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_bytes(b"original")
    output = tmp_path / "out.txt"
    preview = harness.edits.prepare(harness.plan(source, output))
    with pytest.raises(WriteAuthorizationError):
        harness.edits.registry.execute(
            "office.document.create", {"reference_id": str(preview.transaction_id)}
        )
    harness.approve(preview.transaction_id)
    output.write_bytes(b"user conflict")
    with pytest.raises(OfficeError, match="ALREADY_EXISTS"):
        harness.edits.execute(preview.transaction_id)
    assert output.read_bytes() == b"user conflict"
    other = harness.edits.prepare(harness.plan(source, tmp_path / "cancel.txt"))
    harness.approve(other.transaction_id)
    token = CancellationToken()
    token.cancel()
    with pytest.raises(OfficeError, match="CANCELLED"):
        harness.edits.execute(other.transaction_id, token)
    assert not (tmp_path / "cancel.txt").exists()


def test_corrupt_backup_blocks_write(harness: Harness, tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_bytes(b"private original")
    preview = harness.edits.prepare(harness.plan(source, source, OutputMode.EDIT_IN_PLACE))
    harness.approve(preview.transaction_id, inplace=True)
    backed = harness.edits.preview(preview.transaction_id)
    record = harness.repository.backup(backed.backup_id)
    assert b"private original" not in record.path.read_bytes()
    record.path.write_bytes(b"corrupt")
    with pytest.raises(OfficeError, match="BACKUP_CORRUPT"):
        harness.edits.execute(preview.transaction_id)
    assert source.read_bytes() == b"private original"
    assert b"private original" not in (tmp_path / "office.db").read_bytes()
    assert b"private original" not in (tmp_path / "audit.db").read_bytes()


def test_temp_semantic_failure_does_not_modify_original(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "source.txt"
    source.write_bytes(b"original")
    preview = harness.edits.prepare(harness.plan(source, source, OutputMode.EDIT_IN_PLACE))
    harness.approve(preview.transaction_id, inplace=True)
    monkeypatch.setattr(
        harness.codec,
        "parse",
        lambda *args: StructuredDocument(
            format=DocumentFormat.TXT, blocks=(DocumentBlock(reference="body", text="wrong"),)
        ),
    )
    with pytest.raises(OfficeError, match="VERIFICATION_FAILED"):
        harness.edits.execute(preview.transaction_id)
    assert source.read_bytes() == b"original"
    record = harness.repository.transaction(preview.transaction_id)
    assert record.state is OfficeTransactionState.FAILED
    assert record.temporary_path.exists()


def test_backup_quota_prevents_edit(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "source.txt"
    source.write_bytes(b"original")
    preview = harness.edits.prepare(harness.plan(source, source, OutputMode.EDIT_IN_PLACE))
    monkeypatch.setattr(harness.backups, "_limits", OfficeLimits(backup_bytes=1))
    harness.edits.request_backup(preview.transaction_id)
    with pytest.raises(OfficeError, match="QUOTA"):
        harness.edits.confirm_backup(preview.transaction_id, True)
    assert source.read_bytes() == b"original"


def test_restart_invalidates_pending_edit(harness: Harness, tmp_path: Path):
    output = tmp_path / "created.txt"
    preview = harness.edits.prepare(harness.plan(None, output, OutputMode.CREATE_NEW))
    harness.approve(preview.transaction_id)
    other = OfficeRepository(tmp_path / "office.db")
    try:
        assert other.transaction(preview.transaction_id).state is OfficeTransactionState.INTERRUPTED
        with pytest.raises(OfficeError):
            harness.edits.execute(preview.transaction_id)
    finally:
        other.close()
    assert not output.exists()
