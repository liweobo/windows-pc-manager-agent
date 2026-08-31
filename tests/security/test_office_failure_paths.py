from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError
from tests.integration.office.test_edit_flow import Harness
from tests.integration.office.test_edit_flow import harness as harness

from pc_manager_agent.domain.office_documents import (
    DocumentFormat,
    OfficeError,
    OfficeValue,
    ValueKind,
)
from pc_manager_agent.domain.office_plans import OutputMode
from pc_manager_agent.domain.office_transactions import OfficeTransactionState
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.platform_support.windows.office_files import (
    OfficeFileLease,
    _raw,
    office_main_is_elevated,
)
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.office_tools.write import (
    OfficeWriteGuard,
    OfficeWriteRequest,
    OfficeWriteTool,
)


def test_backup_file_store_rejection_and_failed_readback(
    harness: Harness, tmp_path: Path, monkeypatch
):
    source = tmp_path / "source.txt"
    source.write_bytes(b"test")
    identity = harness.read(source).reference.identity
    (tmp_path / "backups").write_bytes(b"not a directory")
    with pytest.raises(OfficeError, match="LOCATION_UNSAFE"):
        harness.backups.create(b"test", identity, CancellationToken(), uuid4())
    (tmp_path / "backups").rename(tmp_path / "preserved-non-directory")
    monkeypatch.setattr(harness.backups._protector, "unprotect", lambda _: b"wrong readback")
    with pytest.raises(OfficeError, match="VERIFICATION_FAILED"):
        harness.backups.create(b"test", identity, CancellationToken(), uuid4())


def test_storage_errors_and_compare_exchange_are_fail_closed(
    harness: Harness, tmp_path: Path, monkeypatch
):
    preview = harness.edits.prepare(harness.plan(None, tmp_path / "new.txt", OutputMode.CREATE_NEW))
    repository = harness.repository
    previous = repository.transaction(preview.transaction_id)
    updated = previous.model_copy(update={"state": OfficeTransactionState.CANCELLED})
    repository.change(previous, updated)
    with pytest.raises(OfficeError, match="CONCURRENT"):
        repository.change(previous, updated)
    with pytest.raises(OfficeError, match="NOT_PREVIEWED"):
        repository.begin_write(updated, ((uuid4(), "a", "b"),), datetime.now(UTC))
    with pytest.raises(OfficeError, match="AUTHORITY"):
        repository.begin_write(previous, (), datetime.now(UTC))
    now = datetime.now(UTC)
    identifier = repository.request("a", "test", now + timedelta(minutes=1))
    repository.resolve(identifier, "a", "test", True, now)
    with pytest.raises(OfficeError, match="CONCURRENT"):
        repository.begin_write(previous, ((identifier, "a", "test"),), now)

    def unavailable(*args, **kwargs):
        raise OperationalError("synthetic", {}, RuntimeError("synthetic failure"))

    monkeypatch.setattr(repository._engine, "begin", unavailable)
    for operation in (
        lambda: repository.change(updated, previous),
        lambda: repository.consume(identifier, "a", "test", now),
        lambda: repository.put_transaction(previous),
        lambda: repository.begin_write(previous, ((identifier, "a", "test"),), now),
    ):
        with pytest.raises(OfficeError, match="STORAGE_UNAVAILABLE"):
            operation()
    monkeypatch.setattr(repository._engine, "connect", unavailable)
    with pytest.raises(OfficeError, match="STORAGE_UNAVAILABLE"):
        repository.transaction(previous.transaction_id)


@pytest.mark.parametrize(
    "failure", ["temp_hash", "permissions", "final_identity", "cancel_before_rename"]
)
def test_commit_verification_failures_keep_recovery_material(
    harness: Harness, tmp_path: Path, monkeypatch, failure
):
    source = tmp_path / "original.txt"
    source.write_bytes(b"original")
    preview = harness.edits.prepare(harness.plan(source, source, OutputMode.EDIT_IN_PLACE))
    harness.approve(preview.transaction_id, inplace=True)
    if failure == "temp_hash":
        real = OfficeFileLease.write_new
        monkeypatch.setattr(OfficeFileLease, "write_new", lambda lease, data: real(lease, b"wrong"))
    elif failure == "permissions":
        real_security_digest = OfficeFileLease.security_digest
        monkeypatch.setattr(
            OfficeFileLease,
            "security_digest",
            lambda lease: (
                "0" * 64 if ".pending" in lease.path.name else real_security_digest(lease)
            ),
        )
    elif failure == "final_identity":
        real_identity = OfficeFileLease.identity

        def changed(lease, data, format_):
            observed = real_identity(lease, data, format_)
            if lease.path == source and data == b"changed":
                return observed.model_copy(update={"sha256": "0" * 64})
            return observed

        monkeypatch.setattr(OfficeFileLease, "identity", changed)
    else:
        real_check = harness.edits._validate_bindings
        calls = 0

        def cancel(session, token):
            nonlocal calls
            calls += 1
            real_check(session, token)
            if calls == 2:
                token.cancel()

        monkeypatch.setattr(harness.edits, "_validate_bindings", cancel)
    with pytest.raises(OfficeError):
        harness.edits.execute(preview.transaction_id)
    record = harness.repository.transaction(preview.transaction_id)
    assert record.state in {OfficeTransactionState.CANCELLED, OfficeTransactionState.FAILED}
    if failure == "final_identity":
        assert record.retained_original_path.read_bytes() == b"original"
    else:
        assert source.read_bytes() == b"original"


def test_direct_tool_guard_and_schema_never_grant_authority(harness: Harness, tmp_path: Path):
    preview = harness.edits.prepare(harness.plan(None, tmp_path / "new.txt", OutputMode.CREATE_NEW))
    guard = OfficeWriteGuard(harness.repository)
    authorization = guard.issue(
        preview.transaction_id, preview.plan_id, preview.preview_id, "office.document.create"
    )
    with pytest.raises(OfficeError, match="DISPATCH_CHANGED"):
        guard.require(
            authorization, "office.document.backup", {"reference_id": str(preview.transaction_id)}
        )
    with pytest.raises(OfficeError, match="OUTSIDE_CONFIRMED_SERVICE"):
        harness.edits.registry.execute(
            "office.document.create",
            {"reference_id": str(preview.transaction_id)},
            authorization=authorization,
        )
    for name, risk in (
        ("office.run_python", RiskLevel.R1),
        ("office.document.create", RiskLevel.R0),
    ):
        with pytest.raises(OfficeError):
            OfficeWriteTool(name, risk, lambda identifier, token: identifier)
    with pytest.raises(TypeError):
        OfficeWriteTool(
            "office.document.create", RiskLevel.R1, lambda identifier, token: identifier
        ).execute(OfficeValue(), CancellationToken())
    assert OfficeWriteRequest(reference_id=uuid4()).reference_id


def test_native_hardlink_extra_stream_and_attributes_block(harness: Harness, tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_bytes(b"data")
    files = harness.edits._files
    with files.open(source) as lease:
        identity = lease.identity(b"data", DocumentFormat.TXT)
        for changed in (
            identity.model_copy(update={"hard_links": 2}),
            identity.model_copy(update={"readonly": True}),
            identity.model_copy(
                update={"state": identity.state.model_copy(update={"attributes": 0x4000})}
            ),
        ):
            with pytest.raises(OfficeError):
                lease.require_replaceable(changed)
    # Synthetic NTFS ADS in the test directory only. The adapter must not lose it on replacement.
    Path(str(source) + ":synthetic-stream").write_bytes(b"retained metadata")
    with files.open(source) as lease:
        identity = lease.identity(b"data", DocumentFormat.TXT)
        with pytest.raises(OfficeError, match="EXTRA_STREAMS"):
            lease.require_replaceable(identity)


@pytest.mark.parametrize(
    "kind,value",
    [
        (ValueKind.DATE, "2026-08-31"),
        (ValueKind.DATETIME, "2026-08-31T10:00:00"),
        (ValueKind.BOOLEAN, "false"),
        (ValueKind.EMPTY, ""),
        (ValueKind.FORMULA, "=A1+1"),
    ],
)
def test_typed_identity_document_scalars(kind, value):
    assert OfficeValue(kind=kind, value=value).value == value


def test_resolver_does_not_auto_select_duplicate_names(harness: Harness, tmp_path: Path):
    from pc_manager_agent.office.resolution import DocumentTargetResolver

    source = tmp_path / "same.txt"
    source.write_bytes(b"a")
    result = harness.read(source)
    resolver = DocumentTargetResolver()
    assert len(resolver.candidates("same.txt", (result, result))) == 2
    assert resolver.resolve(result.reference.document_id, (result,)) == result.reference
    for name in ("", "../same.txt", "*"):
        with pytest.raises(OfficeError):
            resolver.candidates(name, (result,))
    with pytest.raises(OfficeError, match="AMBIGUOUS"):
        resolver.resolve(result.reference.document_id, (result, result))
    assert result.document.content_digest()


def test_native_metadata_denials_are_before_content_read(
    harness: Harness, tmp_path: Path, monkeypatch
):
    import win32api
    import win32file
    from pywintypes import error as WindowsApiError

    assert isinstance(office_main_is_elevated(), bool)
    with pytest.raises(OfficeError):
        _raw(Path("relative.txt"))
    source = tmp_path / "test.txt"
    source.write_bytes(b"test")
    files = harness.edits._files
    with files.open(source) as lease:
        identity = lease.identity(b"test", DocumentFormat.TXT)
        with pytest.raises(OfficeError, match="CHANGED_DURING_READ"):
            lease.identity(b"short", DocumentFormat.TXT)
        real_path = win32file.GetFinalPathNameByHandle
        monkeypatch.setattr(win32file, "GetFinalPathNameByHandle", lambda *args: "wrong")
        with pytest.raises(OfficeError, match="PATH_CHANGED"):
            lease.identity(b"test", DocumentFormat.TXT)
        monkeypatch.setattr(win32file, "GetFinalPathNameByHandle", real_path)
        real_info = win32file.GetFileInformationByHandle
        observed = real_info(lease.handle)
        monkeypatch.setattr(
            win32file, "GetFileInformationByHandle", lambda *args: (0x400, *observed[1:])
        )
        with pytest.raises(OfficeError, match="ATTRIBUTES"):
            lease.identity(b"test", DocumentFormat.TXT)
        monkeypatch.setattr(win32file, "GetFileInformationByHandle", real_info)
        monkeypatch.setattr(win32api, "GetVolumeInformation", lambda *args: ("", 0, 0, 0, "FAT32"))
        with pytest.raises(OfficeError, match="NTFS"):
            lease.require_replaceable(identity)

        def failed(*args):
            raise WindowsApiError(5, "test", "synthetic")

        monkeypatch.setattr(win32api, "GetVolumeInformation", failed)
        with pytest.raises(OfficeError, match="EVIDENCE_UNAVAILABLE"):
            lease.require_replaceable(identity)
    (tmp_path / "hardlink.txt").hardlink_to(source)
    with pytest.raises(OfficeError, match="HARDLINK"), files.open(source):
        pytest.fail("Hardlinked content must never become readable")


def test_restore_material_change_preserves_current_output(harness: Harness, tmp_path: Path):
    from pc_manager_agent.authorization.office_documents import OfficeGrantKind

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
    edited.retained_original_path.write_bytes(b"tampered recovery bytes")
    with pytest.raises(OfficeError, match="IDENTITY_CHANGED"):
        harness.edits.execute(restore.transaction_id)
    assert source.read_bytes() == b"changed"
    assert (
        harness.repository.transaction(restore.transaction_id).state
        is OfficeTransactionState.PREVIEWED
    )


@pytest.mark.parametrize("restore", [False, True])
@pytest.mark.parametrize("failure", ["cancel", "rename_error", "final_identity"])
def test_recovery_failure_never_deletes_material_or_claims_completion(
    harness: Harness, tmp_path: Path, monkeypatch, restore, failure
):
    from pc_manager_agent.authorization.office_documents import OfficeGrantKind

    source = tmp_path / "synthetic.txt"
    if restore:
        source.write_bytes(b"original")
    plan = harness.plan(
        source if restore else None,
        source,
        OutputMode.EDIT_IN_PLACE if restore else OutputMode.CREATE_NEW,
    )
    preview = harness.edits.prepare(plan)
    harness.approve(preview.transaction_id, inplace=restore)
    original = harness.edits.execute(preview.transaction_id)
    current = harness.read(source)
    grant = harness.reads.grants.select(source, OfficeGrantKind.OUTPUT)
    prepare = harness.edits.prepare_restore if restore else harness.edits.prepare_undo_created
    recovery = prepare(original.transaction_id, current.reference.document_id, grant.grant_id)
    harness.approve(recovery.transaction_id, inplace=restore)
    if failure == "cancel":
        monkeypatch.setattr(
            harness.edits, "_validate_bindings", lambda session, token: token.cancel()
        )
    elif failure == "rename_error":

        def failed_rename(*args):
            raise RuntimeError("synthetic rename failure")

        monkeypatch.setattr(OfficeFileLease, "rename_absent", failed_rename)
    else:
        observe = OfficeFileLease.identity

        def changed(lease, data, format_):
            identity = observe(lease, data, format_)
            if ".recovered" in lease.path.name or (lease.path == source and data == b"original"):
                return identity.model_copy(update={"sha256": "0" * 64})
            return identity

        monkeypatch.setattr(OfficeFileLease, "identity", changed)
    with pytest.raises(OfficeError):
        harness.edits.execute(recovery.transaction_id)
    record = harness.repository.transaction(recovery.transaction_id)
    assert record.state is (
        OfficeTransactionState.CANCELLED if failure == "cancel" else OfficeTransactionState.FAILED
    )
    assert (
        harness.repository.transaction(original.transaction_id).state
        is OfficeTransactionState.COMPLETED
    )
    if failure == "final_identity":
        assert record.retained_original_path.exists()
    else:
        assert source.read_bytes() == (b"changed" if restore else b"created")


def test_direct_commit_rejects_missing_authority_and_arbitrary_recovery_paths(
    harness: Harness, tmp_path: Path
):
    plan = harness.plan(None, tmp_path / "new.txt", OutputMode.CREATE_NEW)
    preview = harness.edits.prepare(plan)
    transaction = harness.repository.transaction(preview.transaction_id)
    commit = harness.edits._committer
    token = CancellationToken()
    with pytest.raises(OfficeError, match="NOT_CONFIRMED"):
        commit.execute(transaction, plan, b"created", plan.initial_document, token, lambda: None)
    with pytest.raises(OfficeError, match="NOT_CONFIRMED"):
        commit.undo_created(transaction, token, lambda: None)
    with pytest.raises(OfficeError, match="MATERIAL_REQUIRED"):
        commit.restore_original(
            transaction, transaction, plan.initial_document, token, lambda: None
        )
    with pytest.raises(OfficeError, match="MATERIAL_REQUIRED"):
        commit.validate_restore_material(transaction, token)
    source = tmp_path / "source.txt"
    source.write_bytes(b"original")
    identity = harness.read(source).reference.identity
    altered = transaction.model_copy(
        update={
            "source": identity,
            "state": OfficeTransactionState.CONFIRMED,
            "retained_original_path": tmp_path / "not-authorized.txt",
        }
    )
    with pytest.raises(OfficeError, match="PATH_CHANGED"):
        commit.validate_restore_material(altered, token)
    with pytest.raises(OfficeError, match="PATH_CHANGED"):
        commit.restore_original(altered, altered, plan.initial_document, token, lambda: None)


def test_destination_appearing_after_consumption_is_not_overwritten(
    harness: Harness, tmp_path: Path, monkeypatch
):
    destination = tmp_path / "result.txt"
    preview = harness.edits.prepare(harness.plan(None, destination, OutputMode.CREATE_NEW))
    harness.approve(preview.transaction_id)
    monkeypatch.setattr(
        harness.edits,
        "_validate_bindings",
        lambda session, token: destination.write_bytes(b"other"),
    )
    with pytest.raises(OfficeError, match="ALREADY_EXISTS"):
        harness.edits.execute(preview.transaction_id)
    assert destination.read_bytes() == b"other"


def test_permission_change_invalidates_confirmed_edit(
    harness: Harness, tmp_path: Path, monkeypatch
):
    source = tmp_path / "permissions.txt"
    source.write_bytes(b"original")
    preview = harness.edits.prepare(harness.plan(source, source, OutputMode.EDIT_IN_PLACE))
    harness.approve(preview.transaction_id, inplace=True)
    # Simulate a new owner/DACL observation without modifying host/test file permissions.
    observe = OfficeFileLease.security_digest
    monkeypatch.setattr(
        OfficeFileLease,
        "security_digest",
        lambda lease: "0" * 64 if lease.path == source else observe(lease),
    )
    with pytest.raises(OfficeError, match="IDENTITY_CHANGED"):
        harness.edits.execute(preview.transaction_id)
    assert source.read_bytes() == b"original"
