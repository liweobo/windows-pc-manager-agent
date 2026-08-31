"""Office-only Preview, backup, confirmation and verified reference-only write orchestration."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock
from time import monotonic
from uuid import UUID, uuid4

from pc_manager_agent.audit.office_documents import OfficeAudit
from pc_manager_agent.authorization.office_documents import OfficeGrantKind, OfficePathGrants
from pc_manager_agent.backup.office_documents import DocumentBackupService
from pc_manager_agent.config.office import OfficeLimits
from pc_manager_agent.confirmation.office_documents import DocumentConfirmations
from pc_manager_agent.domain.office_documents import OfficeError, StructuredDocument, office_digest
from pc_manager_agent.domain.office_plans import (
    DocumentEditPlan,
    DocumentPreview,
    OutputMode,
    OutputSpecification,
)
from pc_manager_agent.domain.office_transactions import OfficeTransaction, OfficeTransactionState
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.office.commit import DocumentCommitter, OfficeCodec
from pc_manager_agent.office.transform import document_diff, transform_documents
from pc_manager_agent.orchestration.office_documents import OfficeDocumentService
from pc_manager_agent.persistence.office_documents import OfficeRepository
from pc_manager_agent.platform_support.windows.office_files import WindowsOfficeFiles
from pc_manager_agent.safety.office.editing import (
    edit_risk,
    require_roundtrip,
    validate_edit_plan,
    validate_structure,
)
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.office_tools.write import (
    OfficeWriteGuard,
    OfficeWriteRequest,
    OfficeWriteTool,
)
from pc_manager_agent.tools.registry import ToolRegistry


@dataclass
class PreparedOfficeEdit:
    """Volatile content plus immutable Preview; never store this object in audit/SQLite."""

    plan: DocumentEditPlan
    preview: DocumentPreview
    output: bytes
    document: StructuredDocument
    backup_confirmation_id: UUID | None = None
    plan_confirmation_id: UUID | None = None
    runtime_confirmation_id: UUID | None = None
    recovery_of: UUID | None = None
    dispatch_expires_at: datetime | None = None


class OfficeEditService:
    """Deterministic authority owner; UI and models never receive the commit capability."""

    def __init__(
        self,
        reads: OfficeDocumentService,
        files: WindowsOfficeFiles,
        codec: OfficeCodec,
        repository: OfficeRepository,
        backups: DocumentBackupService,
        confirmations: DocumentConfirmations,
        audit: OfficeAudit,
        limits: OfficeLimits,
        is_elevated: Callable[[], bool],
    ) -> None:
        self._reads, self._files, self._codec = reads, files, codec
        self.grants: OfficePathGrants = reads.grants
        self._repository, self._backups = repository, backups
        self._confirmations, self._audit, self.limits = confirmations, audit, limits
        self._is_elevated = is_elevated
        self._pending: dict[UUID, PreparedOfficeEdit] = {}
        self._lock = RLock()
        self._active: tuple[UUID, str] | None = None
        self._guard = OfficeWriteGuard(repository)
        self.registry = ToolRegistry(self._guard)
        self.registry.register(
            OfficeWriteTool("office.document.backup", RiskLevel.R1, self._backup)
        )
        for name, risk in (
            ("office.document.create", RiskLevel.R1),
            ("office.document.undo_created", RiskLevel.R1),
            ("office.document.apply_edit", RiskLevel.R2_HIGH_IMPACT),
            ("office.document.restore", RiskLevel.R2_HIGH_IMPACT),
        ):
            self.registry.register(OfficeWriteTool(name, risk, self._commit))
        self._committer = DocumentCommitter(files, codec, repository, audit, limits)

    def prepare(
        self, plan: DocumentEditPlan, cancellation: CancellationToken | None = None
    ) -> DocumentPreview:
        """Compile a plan from exact previously read references, without modifying any file."""
        plan = DocumentEditPlan.model_validate_json(plan.model_dump_json())
        if plan.output.mode not in {
            OutputMode.CREATE_NEW,
            OutputMode.SAVE_AS,
            OutputMode.EDIT_IN_PLACE,
        }:
            raise OfficeError("RECOVERY_REQUIRES_INDEPENDENT_PLAN")
        token = cancellation or CancellationToken()
        with self._lock:
            sources = self._validate(plan, token)
            views = tuple(self._reads.result(item.document_id).document for item in plan.inputs)
            output_view = transform_documents(views, plan, self.limits)
            validate_structure(output_view, self.limits)
            original = (
                sources[0] if len(sources) == 1 and views[0].format == output_view.format else None
            )
            data = self._codec.render(output_view, original, token)
            reopened = self._codec.parse(data, output_view.format, token, output_view.delimiter)
            require_roundtrip(output_view, reopened)
            return self._publish(plan, reopened, data, views[0] if len(views) == 1 else None)

    def preview(self, transaction_id: UUID) -> DocumentPreview:
        """Return the current immutable preview; backup creation invalidates its predecessor."""
        return self._session(transaction_id).preview

    def request_backup(self, transaction_id: UUID) -> UUID:
        """Request a separate exact R1 backup copy; this cannot approve an in-place edit."""
        with self._lock:
            session = self._session(transaction_id)
            if (
                session.plan.output.mode not in {OutputMode.EDIT_IN_PLACE, OutputMode.RESTORE}
                or session.preview.backup_id
            ):
                raise OfficeError("BACKUP_NOT_REQUIRED_OR_ALREADY_EXISTS")
            self._validate(session.plan, CancellationToken())
            identifier = self._confirmations.request(
                session.preview.canonical_digest(), "BACKUP", session.preview.expires_at
            )
            session.backup_confirmation_id = identifier
            return identifier

    def confirm_backup(
        self, transaction_id: UUID, approved: bool, cancellation: CancellationToken | None = None
    ) -> DocumentPreview:
        """Resolve explicit backup consent, create a verified copy, then publish a new Preview."""
        with self._lock:
            session = self._session(transaction_id)
            if session.backup_confirmation_id is None:
                raise OfficeError("BACKUP_CONFIRMATION_REQUIRED")
            binding = session.preview.canonical_digest()
            self._confirmations.resolve(session.backup_confirmation_id, binding, "BACKUP", approved)
            if not approved:
                return session.preview
            self._confirmations.consume(session.backup_confirmation_id, binding, "BACKUP")
            self._dispatch(session, "office.document.backup", cancellation or CancellationToken())
            return session.preview

    def request_plan_confirmation(self, transaction_id: UUID) -> UUID:
        """Prepare exact edit approval only after required backup and current identity pass."""
        with self._lock:
            session = self._session(transaction_id)
            self._validate_ready(session, CancellationToken())
            identifier = self._confirmations.request(
                session.preview.canonical_digest(), "EDIT_PLAN", session.preview.expires_at
            )
            session.plan_confirmation_id = identifier
            session.runtime_confirmation_id = None
            return identifier

    def confirm_plan(self, transaction_id: UUID, approved: bool) -> None:
        """Resolve the exact plan without performing or implicitly approving an R2 write."""
        with self._lock:
            session = self._session(transaction_id)
            self._validate_ready(session, CancellationToken())
            if session.plan_confirmation_id is None:
                raise OfficeError("EDIT_PLAN_CONFIRMATION_REQUIRED")
            self._confirmations.resolve(
                session.plan_confirmation_id,
                session.preview.canonical_digest(),
                "EDIT_PLAN",
                approved,
            )

    def request_immediate_confirmation(self, transaction_id: UUID) -> UUID:
        """Freshly validate before issuing a short-lived independent object-specific R2 consent."""
        with self._lock:
            session = self._session(transaction_id)
            self._validate_ready(session, CancellationToken())
            if session.preview.risk_level is RiskLevel.R1 or session.plan_confirmation_id is None:
                raise OfficeError("IMMEDIATE_CONFIRMATION_NOT_READY")
            expires = min(
                session.preview.expires_at,
                self._confirmations.now() + timedelta(seconds=self.limits.runtime_ttl_seconds),
            )
            identifier = self._confirmations.request(
                session.preview.canonical_digest(), "EDIT_NOW", expires
            )
            session.runtime_confirmation_id = identifier
            return identifier

    def confirm_immediate(self, transaction_id: UUID, approved: bool) -> None:
        """Resolve only the current Fresh Preview; a missing/expired/changed one cannot execute."""
        with self._lock:
            session = self._session(transaction_id)
            self._validate_ready(session, CancellationToken())
            if session.runtime_confirmation_id is None:
                raise OfficeError("IMMEDIATE_CONFIRMATION_REQUIRED")
            self._confirmations.resolve(
                session.runtime_confirmation_id,
                session.preview.canonical_digest(),
                "EDIT_NOW",
                approved,
            )

    def execute(
        self, transaction_id: UUID, cancellation: CancellationToken | None = None
    ) -> OfficeTransaction:
        """Consume both approvals atomically, then dispatch one fixed tool; never auto-retry."""
        token = cancellation or CancellationToken()
        with self._lock:
            session = self._session(transaction_id)
            self._validate_ready(session, token)
            if session.plan_confirmation_id is None:
                raise OfficeError("EDIT_PLAN_CONFIRMATION_REQUIRED")
            binding = session.preview.canonical_digest()
            approvals = [(session.plan_confirmation_id, binding, "EDIT_PLAN")]
            if session.preview.risk_level is not RiskLevel.R1:
                if session.runtime_confirmation_id is None:
                    raise OfficeError("IMMEDIATE_CONFIRMATION_REQUIRED")
                approvals.append((session.runtime_confirmation_id, binding, "EDIT_NOW"))
            transaction = self._repository.transaction(transaction_id)
            self._audit.record(
                "write.authority_consuming", str(transaction_id), risk=session.preview.risk_level
            )
            self._repository.begin_write(transaction, tuple(approvals), self._confirmations.now())
            session.dispatch_expires_at = min(
                session.preview.expires_at,
                self._confirmations.now() + timedelta(seconds=self.limits.runtime_ttl_seconds),
            )
            name = (
                "office.document.restore"
                if session.plan.output.mode is OutputMode.RESTORE
                else (
                    "office.document.create"
                    if session.preview.risk_level is RiskLevel.R1
                    else "office.document.apply_edit"
                )
            )
            if (
                session.plan.output.mode is OutputMode.UNDO_CREATED
                and session.preview.risk_level is RiskLevel.R1
            ):
                name = "office.document.undo_created"
            try:
                started = monotonic()
                self._dispatch(session, name, token)
                return self._repository.transaction(transaction_id)
            finally:
                self._pending.pop(transaction_id, None)
                observed = self._repository.transaction(transaction_id)
                self._audit.record(
                    "write.finished",
                    str(transaction_id),
                    risk=observed.risk_level,
                    code=observed.error_code,
                    transaction=observed,
                    tool_name=name,
                    duration_ms=int((monotonic() - started) * 1000),
                )

    def history(self) -> tuple[OfficeTransaction, ...]:
        """Return metadata and verification state, never bodies or resumable commands."""
        return self._repository.recent()

    def cancel(self, transaction_id: UUID) -> None:
        """Invalidate a pending Preview and all usable session authority; never delete material."""
        with self._lock:
            previous = self._repository.transaction(transaction_id)
            if previous.state is not OfficeTransactionState.PREVIEWED:
                raise OfficeError("OFFICE_CANCELLATION_REQUIRES_PENDING_PREVIEW")
            self._repository.change(
                previous, previous.model_copy(update={"state": OfficeTransactionState.CANCELLED})
            )
            self._pending.pop(transaction_id, None)
            self._audit.record("edit.cancelled", str(transaction_id), risk=previous.risk_level)

    def prepare_undo_created(
        self,
        original_id: UUID,
        current_document_id: UUID,
        destination_grant_id: UUID,
        cancellation: CancellationToken | None = None,
    ) -> DocumentPreview:
        """Prepare a new exact R1/R2 plan to retain, not delete, an unchanged created result."""
        token = cancellation or CancellationToken()
        with self._lock:
            original = self._repository.transaction(original_id)
            current = self._reads.result(current_document_id)
            if (
                original.state is not OfficeTransactionState.COMPLETED
                or original.result != current.reference.identity
                or original.mode not in {OutputMode.CREATE_NEW, OutputMode.SAVE_AS}
            ):
                raise OfficeError("UNDO_CONFLICT")
            now = self._confirmations.now()
            plan = DocumentEditPlan(
                inputs=(current.reference,),
                output=OutputSpecification(
                    mode=OutputMode.UNDO_CREATED,
                    destination_grant_id=destination_grant_id,
                    format=current.document.format,
                ),
                created_at=now,
                expires_at=now + timedelta(seconds=self.limits.preview_ttl_seconds),
                policy_digest=office_digest(self.limits),
            )
            data = self._validate(plan, token)[0]
            return self._publish(
                plan, current.document, data, current.document, recovery_of=original_id
            )

    def prepare_restore(
        self,
        original_id: UUID,
        current_document_id: UUID,
        destination_grant_id: UUID,
        cancellation: CancellationToken | None = None,
    ) -> DocumentPreview:
        """Prepare independent restoration only while the current file exactly equals our result."""
        token = cancellation or CancellationToken()
        with self._lock:
            original = self._repository.transaction(original_id)
            current = self._reads.result(current_document_id)
            if (
                original.state is not OfficeTransactionState.COMPLETED
                or original.backup_id is None
                or original.result != current.reference.identity
                or original.mode not in {OutputMode.EDIT_IN_PLACE, OutputMode.RESTORE}
            ):
                raise OfficeError("RESTORE_CONFLICT")
            data = self._backups.restore_bytes(original.backup_id, token)
            view = self._codec.parse(
                data, current.document.format, token, current.document.delimiter
            )
            validate_structure(view, self.limits)
            now = self._confirmations.now()
            plan = DocumentEditPlan(
                inputs=(current.reference,),
                output=OutputSpecification(
                    mode=OutputMode.RESTORE,
                    destination_grant_id=destination_grant_id,
                    format=current.document.format,
                ),
                created_at=now,
                expires_at=now + timedelta(seconds=self.limits.preview_ttl_seconds),
                policy_digest=office_digest(self.limits),
            )
            self._validate(plan, token)
            self._committer.validate_restore_material(original, token)
            return self._publish(plan, view, data, current.document, recovery_of=original_id)

    def _publish(
        self,
        plan: DocumentEditPlan,
        view: StructuredDocument,
        data: bytes,
        before: StructuredDocument | None,
        *,
        recovery_of: UUID | None = None,
        backup_id: UUID | None = None,
    ) -> DocumentPreview:
        if (
            len(self._pending) >= 3
            or sum(len(item.output) for item in self._pending.values()) + len(data)
            > self.limits.max_total_bytes
        ):
            raise OfficeError("OFFICE_PREVIEW_SESSION_LIMIT")
        differences = document_diff(before, view)
        changed_cells = sum(
            item.before_type in {"text", "number", "formula", "boolean", "date", "datetime"}
            or item.after_type in {"text", "number", "formula", "boolean", "date", "datetime"}
            for item in differences
        )
        grant = self.grants.get(plan.output.destination_grant_id, OfficeGrantKind.OUTPUT)
        risk = edit_risk(plan, changed_cells, self.limits)
        warnings = (
            ["FORMULAS_NOT_RECALCULATED", "NO_FULL_FORMATTING_GUARANTEE"] if view.sheets else []
        )
        if any(item.identity.cloud_sync for item in plan.inputs):
            warnings.append("CLOUD_SYNC_CAUTION")
        if plan.output.mode is OutputMode.UNDO_CREATED:
            warnings.append("MOVE_TO_RECOVERY_SIBLING_NOT_DELETE")
        backup = self._repository.backup(backup_id) if backup_id else None
        recovery_backup = None
        if recovery_of is not None and plan.output.mode is OutputMode.RESTORE:
            original_backup_id = self._repository.transaction(recovery_of).backup_id
            if original_backup_id is None:
                raise OfficeError("RESTORE_BACKUP_MISSING")
            recovery_backup = self._repository.backup(original_backup_id)
        preview = DocumentPreview(
            plan_id=plan.plan_id,
            plan_digest=plan.canonical_digest(),
            destination_digest=grant.canonical_digest(),
            output_digest=hashlib.sha256(data).hexdigest(),
            differences=differences,
            warnings=tuple(warnings),
            risk_level=risk,
            source_bytes=sum(item.identity.state.size_bytes for item in plan.inputs),
            changed_cells=changed_cells,
            changed_formulas=sum(
                item.before_type == "formula" or item.after_type == "formula"
                for item in differences
            ),
            backup_id=backup_id,
            backup_digest=office_digest(backup) if backup else None,
            recovery_of=recovery_of,
            recovery_backup_digest=office_digest(recovery_backup) if recovery_backup else None,
            created_at=self._confirmations.now(),
            expires_at=plan.expires_at,
        )
        transaction = OfficeTransaction(
            transaction_id=preview.transaction_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            plan_digest=preview.plan_digest,
            preview_digest=preview.canonical_digest(),
            output_path=grant.path,
            mode=plan.output.mode,
            risk_level=risk,
            source=plan.inputs[0].identity if plan.inputs else None,
            format=plan.output.format,
            operation_kinds=tuple(item.kind for item in plan.operations),
            input_identity_digests=tuple(office_digest(item.identity) for item in plan.inputs),
            backup_id=backup_id,
            recovery_of=recovery_of,
            created_at=preview.created_at,
            expires_at=preview.expires_at,
        )
        self._repository.put_transaction(transaction)
        self._audit.record(
            "edit.previewed",
            str(preview.transaction_id),
            risk=risk,
            count=len(differences),
            transaction=transaction,
        )
        self._pending[preview.transaction_id] = PreparedOfficeEdit(
            plan, preview, data, view, recovery_of=recovery_of
        )
        return preview

    def _validate(self, plan: DocumentEditPlan, token: CancellationToken) -> tuple[bytes, ...]:
        validate_edit_plan(plan, self.limits, self._confirmations.now())
        if self._is_elevated():
            raise OfficeError("ELEVATED_MAIN_BLOCKED")
        grant = self.grants.get(plan.output.destination_grant_id, OfficeGrantKind.OUTPUT)
        if grant.format is not plan.output.format:
            raise OfficeError("OUTPUT_FORMAT_MISMATCH")
        inplace = plan.output.mode in {
            OutputMode.EDIT_IN_PLACE,
            OutputMode.RESTORE,
            OutputMode.UNDO_CREATED,
        }
        if inplace:
            if grant.path != plan.inputs[0].identity.state.path:
                raise OfficeError("INPLACE_TARGET_CHANGED")
        elif grant.path.exists() or any(
            item.identity.state.path == grant.path for item in plan.inputs
        ):
            raise OfficeError("OUTPUT_ALREADY_EXISTS")
        sources: list[bytes] = []
        for reference in plan.inputs:
            if self._reads.result(reference.document_id).reference != reference:
                raise OfficeError("DOCUMENT_REFERENCE_CHANGED")
            source_grant = self.grants.get(reference.grant_id, OfficeGrantKind.READ)
            if source_grant.path != reference.identity.state.path:
                raise OfficeError("DOCUMENT_GRANT_CHANGED")
            with (
                self._files.pin_parents(source_grant.path),
                self._files.open(source_grant.path) as lease,
            ):
                data = lease.read(self.limits.other_bytes, token)
                if not reference.identity.matches(lease.identity(data, source_grant.format)):
                    raise OfficeError("DOCUMENT_IDENTITY_CHANGED")
                if inplace:
                    lease.require_replaceable(reference.identity)
                sources.append(data)
        if sum(len(data) for data in sources) > self.limits.max_total_bytes:
            raise OfficeError("DOCUMENT_TOTAL_SIZE_LIMIT")
        return tuple(sources)

    def _validate_ready(self, session: PreparedOfficeEdit, token: CancellationToken) -> None:
        self._validate(session.plan, token)
        preview = session.preview
        if preview.plan_digest != session.plan.canonical_digest() or (
            self.grants.get(
                session.plan.output.destination_grant_id, OfficeGrantKind.OUTPUT
            ).canonical_digest()
            != preview.destination_digest
        ):
            raise OfficeError("EDIT_PREVIEW_CHANGED")
        if hashlib.sha256(session.output).hexdigest() != preview.output_digest:
            raise OfficeError("EDIT_OUTPUT_CHANGED")
        if session.plan.output.mode is OutputMode.UNDO_CREATED:
            if preview.recovery_of is None:
                raise OfficeError("UNDO_REFERENCE_REQUIRED")
            original = self._repository.transaction(preview.recovery_of)
            if (
                original.state is not OfficeTransactionState.COMPLETED
                or original.result != session.plan.inputs[0].identity
            ):
                raise OfficeError("UNDO_CONFLICT")
        if session.plan.output.mode in {OutputMode.EDIT_IN_PLACE, OutputMode.RESTORE}:
            if preview.backup_id is None:
                raise OfficeError("VERIFIED_BACKUP_REQUIRED")
            backup = self._repository.backup(preview.backup_id)
            if office_digest(backup) != preview.backup_digest:
                raise OfficeError("BACKUP_BINDING_CHANGED")
            self._backups.restore_bytes(backup.backup_id, token)
            if (
                backup.source != session.plan.inputs[0].identity
                or backup.transaction_id != preview.transaction_id
            ):
                raise OfficeError("BACKUP_SOURCE_CHANGED")
            if session.plan.output.mode is OutputMode.RESTORE:
                if preview.recovery_of is None:
                    raise OfficeError("RESTORE_REFERENCE_REQUIRED")
                original = self._repository.transaction(preview.recovery_of)
                if (
                    original.state is not OfficeTransactionState.COMPLETED
                    or original.result != session.plan.inputs[0].identity
                    or original.backup_id is None
                ):
                    raise OfficeError("RESTORE_CONFLICT")
                if (
                    office_digest(self._repository.backup(original.backup_id))
                    != preview.recovery_backup_digest
                ):
                    raise OfficeError("RESTORE_BACKUP_CHANGED")
                if self._backups.restore_bytes(original.backup_id, token) != session.output:
                    raise OfficeError("RESTORE_CONTENT_CHANGED")
                self._committer.validate_restore_material(original, token)

    def _backup(self, identifier: UUID, token: CancellationToken) -> UUID:
        self._require_active(identifier, "office.document.backup")
        session = self._session(identifier)
        sources = self._validate(session.plan, token)
        self._audit.record("backup.starting", str(identifier), risk=RiskLevel.R1)
        backup = self._backups.create(
            sources[0], session.plan.inputs[0].identity, token, identifier
        )
        preview = session.preview.model_copy(
            update={
                "preview_id": uuid4(),
                "backup_id": backup.backup_id,
                "backup_digest": office_digest(backup),
            }
        )
        previous = self._repository.transaction(identifier)
        self._repository.change(
            previous,
            previous.model_copy(
                update={
                    "preview_id": preview.preview_id,
                    "preview_digest": preview.canonical_digest(),
                    "backup_id": backup.backup_id,
                }
            ),
        )
        session.preview = preview
        session.plan_confirmation_id = session.runtime_confirmation_id = None
        self._audit.record(
            "backup.verified", str(identifier), risk=RiskLevel.R1, size=backup.size_bytes
        )
        return backup.backup_id

    def _commit(self, identifier: UUID, token: CancellationToken) -> UUID:
        if (
            self._active is None
            or self._active[0] != identifier
            or self._active[1] == "office.document.backup"
        ):
            raise OfficeError("OFFICE_DISPATCH_OUTSIDE_CONFIRMED_SERVICE")
        session = self._session(identifier)
        if session.plan.output.mode is OutputMode.UNDO_CREATED:
            self._committer.undo_created(
                self._repository.transaction(identifier),
                token,
                lambda: self._validate_bindings(session, token),
            )
        elif session.plan.output.mode is OutputMode.RESTORE and session.recovery_of is not None:
            self._committer.restore_original(
                self._repository.transaction(identifier),
                self._repository.transaction(session.recovery_of),
                session.document,
                token,
                lambda: self._validate_bindings(session, token),
            )
        else:
            self._committer.execute(
                self._repository.transaction(identifier),
                session.plan,
                session.output,
                session.document,
                token,
                lambda: self._validate_bindings(session, token),
            )
        if session.recovery_of is not None:
            previous = self._repository.transaction(session.recovery_of)
            self._repository.change(
                previous, previous.model_copy(update={"state": OfficeTransactionState.RESTORED})
            )
        return identifier

    def _validate_bindings(self, session: PreparedOfficeEdit, token: CancellationToken) -> None:
        # Committer already holds the originals with DELETE access, so reopening
        # them here would break Windows share checks. Revalidate grants and consent
        # binding here; it independently rehashes through those existing handles.
        validate_edit_plan(session.plan, self.limits, self._confirmations.now())
        if (
            session.dispatch_expires_at is None
            or self._confirmations.now() >= session.dispatch_expires_at
        ):
            raise OfficeError("OFFICE_DISPATCH_EXPIRED")
        if token.is_cancelled or self._is_elevated():
            raise OfficeError("DOCUMENT_CANCELLED_OR_ELEVATED")
        for reference in session.plan.inputs:
            self.grants.get(reference.grant_id, OfficeGrantKind.READ)
        grant = self.grants.get(session.plan.output.destination_grant_id, OfficeGrantKind.OUTPUT)
        if grant.canonical_digest() != session.preview.destination_digest:
            raise OfficeError("OUTPUT_GRANT_CHANGED")

    def _dispatch(self, session: PreparedOfficeEdit, name: str, token: CancellationToken) -> None:
        identifier = session.preview.transaction_id
        authorization = self._guard.issue(
            identifier, session.plan.plan_id, session.preview.preview_id, name
        )
        self._active = (identifier, name)
        try:
            self.registry.execute(
                name,
                OfficeWriteRequest(reference_id=identifier).model_dump(mode="json"),
                token,
                authorization,
            )
        finally:
            self._active = None

    def _require_active(self, identifier: UUID, name: str) -> None:
        if self._active != (identifier, name):
            raise OfficeError("OFFICE_DISPATCH_OUTSIDE_CONFIRMED_SERVICE")

    def _session(self, identifier: UUID) -> PreparedOfficeEdit:
        try:
            return self._pending[identifier]
        except KeyError as exc:
            raise OfficeError("OFFICE_EDIT_PREVIEW_MISSING") from exc
