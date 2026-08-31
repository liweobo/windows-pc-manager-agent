"""Handle-bound two-rename commits; no overwrite, delete, retry or automatic recovery."""

import hashlib
from collections.abc import Callable
from contextlib import ExitStack
from typing import Protocol

from pc_manager_agent.audit.office_documents import OfficeAudit
from pc_manager_agent.config.office import OfficeLimits
from pc_manager_agent.domain.office_documents import (
    DocumentFormat,
    OfficeDocumentIdentity,
    OfficeError,
    StructuredDocument,
)
from pc_manager_agent.domain.office_plans import DocumentEditPlan, OutputMode
from pc_manager_agent.domain.office_transactions import OfficeTransaction, OfficeTransactionState
from pc_manager_agent.persistence.office_documents import OfficeRepository
from pc_manager_agent.platform_support.windows.office_files import (
    OfficeFileLease,
    WindowsOfficeFiles,
)
from pc_manager_agent.safety.office.editing import require_roundtrip
from pc_manager_agent.tools.manifest import CancellationToken


class OfficeCodec(Protocol):
    """Finite resource-bounded parser/serializer contract, with no path authority."""

    def parse(
        self,
        data: bytes,
        format_: DocumentFormat,
        cancellation: CancellationToken,
        delimiter: str = ",",
    ) -> StructuredDocument:
        """Parse exact authorized bytes to structured data."""
        ...

    def render(
        self, document: StructuredDocument, original: bytes | None, cancellation: CancellationToken
    ) -> bytes:
        """Return a serialized output without opening any path."""
        ...


class DocumentCommitter:
    """Commit an already consumed exact transaction and independently verify its output."""

    def __init__(
        self,
        files: WindowsOfficeFiles,
        codec: OfficeCodec,
        repository: OfficeRepository,
        audit: OfficeAudit,
        limits: OfficeLimits,
    ) -> None:
        self._files, self._codec, self._repository = files, codec, repository
        self._audit, self._limits = audit, limits

    def execute(
        self,
        transaction: OfficeTransaction,
        plan: DocumentEditPlan,
        output: bytes,
        expected: StructuredDocument,
        token: CancellationToken,
        final_check: Callable[[], None],
    ) -> OfficeTransaction:
        """Write new temp, reopen/verify, revalidate source, commit without replacement, verify."""
        if transaction.state is not OfficeTransactionState.CONFIRMED:
            raise OfficeError("OFFICE_WRITE_NOT_CONFIRMED")
        destination = transaction.output_path
        temporary = destination.with_name(
            f".pcma-{transaction.transaction_id}.pending{destination.suffix}"
        )
        inplace = plan.output.mode in {OutputMode.EDIT_IN_PLACE, OutputMode.RESTORE}
        retained = (
            destination.with_name(
                f".pcma-{transaction.transaction_id}.original{destination.suffix}"
            )
            if inplace
            else None
        )
        journal = transaction.model_copy(
            update={"temporary_path": temporary, "retained_original_path": retained}
        )
        self._repository.change(transaction, journal)
        try:
            with ExitStack() as stack:
                final_check()
                stack.enter_context(self._files.pin_parents(destination))
                sources: list[tuple[OfficeFileLease, OfficeDocumentIdentity]] = []
                for reference in plan.inputs:
                    stack.enter_context(self._files.pin_parents(reference.identity.state.path))
                    lease = stack.enter_context(
                        self._files.open(reference.identity.state.path, mutable=inplace)
                    )
                    self._require_source(lease, reference.identity, token)
                    if inplace:
                        lease.require_replaceable(reference.identity)
                    sources.append((lease, reference.identity))
                if not inplace and destination.exists():
                    raise OfficeError("OUTPUT_ALREADY_EXISTS")
                self._cancel(token)
                journal = self._state(journal, OfficeTransactionState.WRITING_TEMP)
                self._audit.record(
                    "write.temp_starting",
                    str(journal.transaction_id),
                    risk=journal.risk_level,
                    size=len(output),
                )
                with self._files.open(temporary, create=True) as temp:
                    temp.write_new(output)
                journal = self._state(journal, OfficeTransactionState.VERIFYING_TEMP)
                with self._files.open(temporary, mutable=True) as temp:
                    written = temp.read(self._limits.other_bytes, token)
                    if hashlib.sha256(written).digest() != hashlib.sha256(output).digest():
                        raise OfficeError("TEMP_HASH_VERIFICATION_FAILED")
                    temp_identity = temp.identity(written, expected.format)
                    temp.require_replaceable(temp_identity)
                    require_roundtrip(
                        expected,
                        self._codec.parse(written, expected.format, token, expected.delimiter),
                    )
                    if inplace and temp.security_digest() != sources[0][0].security_digest():
                        raise OfficeError("CUSTOM_DOCUMENT_PERMISSIONS_BLOCKED")
                    # All ancestors and original handles remain pinned throughout
                    # the final check and both renames. ReplaceIfExists is FALSE.
                    final_check()
                    for source, identity in sources:
                        self._require_source(source, identity, token)
                    self._cancel(token)
                    journal = self._state(journal, OfficeTransactionState.COMMITTING)
                    self._audit.record(
                        "write.committing", str(journal.transaction_id), risk=journal.risk_level
                    )
                    if retained is not None:
                        sources[0][0].rename_absent(retained)
                    self._cancel(token)
                    temp.rename_absent(destination)
                # A successful rename is not success. Close/reopen the final name,
                # require the temp's file ID/hash and repeat semantic verification.
                with self._files.open(destination) as final:
                    data = final.read(self._limits.other_bytes, token)
                    identity = final.identity(data, expected.format)
                    if (
                        identity.state.file_id != temp_identity.state.file_id
                        or identity.state.volume_serial != temp_identity.state.volume_serial
                        or identity.sha256 != temp_identity.sha256
                    ):
                        raise OfficeError("OUTPUT_IDENTITY_VERIFICATION_FAILED")
                    require_roundtrip(
                        expected,
                        self._codec.parse(data, expected.format, token, expected.delimiter),
                    )
                self._audit.record(
                    "write.verified",
                    str(journal.transaction_id),
                    risk=journal.risk_level,
                    size=len(data),
                )
                updated = journal.model_copy(
                    update={"state": OfficeTransactionState.COMPLETED, "result": identity}
                )
                self._repository.change(journal, updated)
                return updated
        except Exception as exc:
            # Any material written so far is retained. In particular a failure
            # between the two renames is NOT described as an atomic rollback.
            code = exc.code if isinstance(exc, OfficeError) else "DOCUMENT_COMMIT_FAILED"
            state = (
                OfficeTransactionState.CANCELLED
                if token.is_cancelled
                else OfficeTransactionState.FAILED
            )
            failed = journal.model_copy(update={"state": state, "error_code": code})
            self._repository.change(journal, failed)
            self._audit.record(
                "write.failed", str(journal.transaction_id), risk=journal.risk_level, code=code
            )
            raise OfficeError(code) from exc

    def undo_created(
        self,
        transaction: OfficeTransaction,
        token: CancellationToken,
        final_check: Callable[[], None],
    ) -> OfficeTransaction:
        """Move one unchanged Agent-created output to a unique retained sibling; never delete."""
        if transaction.state is not OfficeTransactionState.CONFIRMED or transaction.source is None:
            raise OfficeError("OFFICE_UNDO_NOT_CONFIRMED")
        source = transaction.source
        retained = source.state.path.with_name(
            f".pcma-{transaction.transaction_id}.recovered{source.state.path.suffix}"
        )
        journal = transaction.model_copy(update={"retained_original_path": retained})
        self._repository.change(transaction, journal)
        try:
            with self._files.pin_parents(source.state.path):
                with self._files.open(source.state.path, mutable=True) as lease:
                    self._require_source(lease, source, token)
                    lease.require_replaceable(source)
                    final_check()
                    self._cancel(token)
                    journal = self._state(journal, OfficeTransactionState.COMMITTING)
                    self._audit.record(
                        "undo.committing", str(journal.transaction_id), risk=journal.risk_level
                    )
                    lease.rename_absent(retained)
                with self._files.open(retained) as lease:
                    data = lease.read(self._limits.other_bytes, token)
                    observed = lease.identity(data, source.format)
                    if (
                        observed.sha256 != source.sha256
                        or observed.state.file_id != source.state.file_id
                        or observed.state.volume_serial != source.state.volume_serial
                        or source.state.path.exists()
                    ):
                        raise OfficeError("UNDO_VERIFICATION_FAILED")
                self._audit.record(
                    "undo.verified", str(journal.transaction_id), risk=journal.risk_level
                )
                updated = journal.model_copy(
                    update={"state": OfficeTransactionState.COMPLETED, "result": observed}
                )
                self._repository.change(journal, updated)
                return updated
        except Exception as exc:
            code = exc.code if isinstance(exc, OfficeError) else "DOCUMENT_UNDO_FAILED"
            self._repository.change(
                journal,
                journal.model_copy(
                    update={
                        "state": OfficeTransactionState.CANCELLED
                        if token.is_cancelled
                        else OfficeTransactionState.FAILED,
                        "error_code": code,
                    }
                ),
            )
            self._audit.record(
                "undo.failed", str(journal.transaction_id), risk=journal.risk_level, code=code
            )
            raise OfficeError(code) from exc

    def validate_restore_material(
        self, original: OfficeTransaction, token: CancellationToken
    ) -> OfficeDocumentIdentity:
        """Check the exact retained original before Preview/confirmation, without changing it."""
        if original.source is None or original.retained_original_path is None:
            raise OfficeError("RESTORE_ORIGINAL_MATERIAL_REQUIRED")
        destination = original.output_path
        material = destination.with_name(
            f".pcma-{original.transaction_id}.original{destination.suffix}"
        )
        if material != original.retained_original_path:
            raise OfficeError("RESTORE_MATERIAL_PATH_CHANGED")
        identity = original.source.model_copy(
            update={"state": original.source.state.model_copy(update={"path": material})}
        )
        with self._files.pin_parents(material), self._files.open(material) as lease:
            self._require_source(lease, identity, token)
            lease.require_replaceable(identity)
        return identity

    def restore_original(
        self,
        transaction: OfficeTransaction,
        original: OfficeTransaction,
        expected: StructuredDocument,
        token: CancellationToken,
        final_check: Callable[[], None],
    ) -> OfficeTransaction:
        """Restore the retained ORIGINAL object, preserving identity, times and permissions."""
        if (
            transaction.state is not OfficeTransactionState.CONFIRMED
            or transaction.source is None
            or original.source is None
            or original.retained_original_path is None
        ):
            raise OfficeError("RESTORE_ORIGINAL_MATERIAL_REQUIRED")
        destination = transaction.output_path
        retained_current = destination.with_name(
            f".pcma-{transaction.transaction_id}.original{destination.suffix}"
        )
        material = original.retained_original_path
        # Derive the recovery path, never trust a database path as arbitrary file authority.
        if material != destination.with_name(
            f".pcma-{original.transaction_id}.original{destination.suffix}"
        ):
            raise OfficeError("RESTORE_MATERIAL_PATH_CHANGED")
        material_identity = original.source.model_copy(
            update={"state": original.source.state.model_copy(update={"path": material})}
        )
        journal = transaction.model_copy(update={"retained_original_path": retained_current})
        self._repository.change(transaction, journal)
        try:
            with self._files.pin_parents(destination):
                with (
                    self._files.open(destination, mutable=True) as current,
                    self._files.open(material, mutable=True) as old,
                ):
                    self._require_source(current, transaction.source, token)
                    self._require_source(old, material_identity, token)
                    old.require_replaceable(material_identity)
                    current.require_replaceable(transaction.source)
                    final_check()
                    self._cancel(token)
                    journal = self._state(journal, OfficeTransactionState.COMMITTING)
                    self._audit.record(
                        "restore.committing", str(journal.transaction_id), risk=journal.risk_level
                    )
                    current.rename_absent(retained_current)
                    self._cancel(token)
                    old.rename_absent(destination)
                with self._files.open(destination) as reopened:
                    data = reopened.read(self._limits.other_bytes, token)
                    observed = reopened.identity(data, expected.format)
                    if observed != original.source:
                        raise OfficeError("RESTORE_ORIGINAL_IDENTITY_VERIFICATION_FAILED")
                    require_roundtrip(
                        expected,
                        self._codec.parse(data, expected.format, token, expected.delimiter),
                    )
                self._audit.record(
                    "restore.verified", str(journal.transaction_id), risk=journal.risk_level
                )
                updated = journal.model_copy(
                    update={"state": OfficeTransactionState.COMPLETED, "result": observed}
                )
                self._repository.change(journal, updated)
                return updated
        except Exception as exc:
            code = exc.code if isinstance(exc, OfficeError) else "DOCUMENT_RESTORE_FAILED"
            self._repository.change(
                journal,
                journal.model_copy(
                    update={
                        "state": OfficeTransactionState.CANCELLED
                        if token.is_cancelled
                        else OfficeTransactionState.FAILED,
                        "error_code": code,
                    }
                ),
            )
            self._audit.record(
                "restore.failed", str(journal.transaction_id), risk=journal.risk_level, code=code
            )
            raise OfficeError(code) from exc

    def _state(
        self, current: OfficeTransaction, state: OfficeTransactionState
    ) -> OfficeTransaction:
        updated = current.model_copy(update={"state": state})
        self._repository.change(current, updated)
        return updated

    def _require_source(
        self, lease: OfficeFileLease, expected: OfficeDocumentIdentity, token: CancellationToken
    ) -> None:
        data = lease.read(self._limits.other_bytes, token)
        if not expected.matches(lease.identity(data, expected.format)):
            raise OfficeError("DOCUMENT_IDENTITY_CHANGED")

    @staticmethod
    def _cancel(token: CancellationToken) -> None:
        if token.is_cancelled:
            raise OfficeError("DOCUMENT_CANCELLED")
