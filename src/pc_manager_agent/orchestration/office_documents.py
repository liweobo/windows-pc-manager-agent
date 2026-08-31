"""Office read/prepare service. Write dispatch is added only through separately verified gates."""

from __future__ import annotations

from datetime import timedelta
from threading import RLock
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.audit.office_documents import OfficeAudit
from pc_manager_agent.authorization.office_documents import OfficeGrantKind, OfficePathGrants
from pc_manager_agent.config.office import OfficeLimits
from pc_manager_agent.confirmation.office_documents import DocumentConfirmations
from pc_manager_agent.domain.office_documents import (
    DocumentFormat,
    OfficeError,
    StructuredDocument,
    office_digest,
)
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.office.resolution import DocumentTargetResolver
from pc_manager_agent.platform_support.windows.office_files import WindowsOfficeFiles
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.office_tools.read import (
    OfficeReadRequest,
    OfficeReadResult,
    OfficeReadTool,
)
from pc_manager_agent.tools.registry import ToolRegistry


class OfficeParser(Protocol):
    """Replaceable bounded parser; tests may provide a deterministic in-memory fake."""

    def parse(
        self,
        data: bytes,
        format_: DocumentFormat,
        cancellation: CancellationToken,
        delimiter: str = ",",
    ) -> StructuredDocument:
        """Parse only already-authorized bytes under configured resource budgets."""
        ...


class OfficeReadPlan(FrozenModel):
    """Metadata-only plan approved before any document body is read."""

    plan_id: UUID = Field(default_factory=uuid4)
    grant_ids: tuple[UUID, ...]
    grant_digests: tuple[str, ...]
    policy_digest: str
    delimiter: str = ","

    def canonical_digest(self) -> str:
        """Bind the exact selected files, CSV dialect and resource policy."""
        return office_digest(self)


class PreparedOfficeRead(FrozenModel):
    """UI-visible R0 plan plus an independent pending confirmation."""

    plan: OfficeReadPlan
    confirmation_id: UUID


class OfficeDocumentService:
    """Keep UI, model and raw tool callers outside exact read authorization."""

    def __init__(
        self,
        grants: OfficePathGrants,
        files: WindowsOfficeFiles,
        parser: OfficeParser,
        confirmations: DocumentConfirmations,
        audit: OfficeAudit,
        limits: OfficeLimits,
    ) -> None:
        self.grants = grants
        self._files = files
        self._parser = parser
        self._confirmations = confirmations
        self._audit = audit
        self.limits = limits
        self._pending: dict[UUID, PreparedOfficeRead] = {}
        self._results: dict[UUID, OfficeReadResult] = {}
        self._cache_bytes = 0
        self._active: OfficeReadPlan | None = None
        self._lock = RLock()
        self.registry = ToolRegistry()
        self.registry.register(OfficeReadTool(self._read_one))

    def prepare_read(self, grant_ids: tuple[UUID, ...], delimiter: str = ",") -> PreparedOfficeRead:
        """Resolve only metadata; prepare exact plan and never read contents before approval."""
        if (
            not grant_ids
            or len(grant_ids) > self.limits.max_files
            or len(set(grant_ids)) != len(grant_ids)
        ):
            raise OfficeError("DOCUMENT_SELECTION_LIMIT_OR_DUPLICATE")
        if delimiter not in {",", ";", "\t"}:
            raise OfficeError("UNSUPPORTED_CSV_DELIMITER")
        grants = tuple(self.grants.get(item, OfficeGrantKind.READ) for item in grant_ids)
        plan = OfficeReadPlan(
            grant_ids=grant_ids,
            grant_digests=tuple(item.canonical_digest() for item in grants),
            policy_digest=office_digest(self.limits),
            delimiter=delimiter,
        )
        with self._lock:
            if len(self._pending) >= 50:
                raise OfficeError("OFFICE_SESSION_LIMIT")
            confirmation_id = self._confirmations.request(
                plan.canonical_digest(),
                "READ",
                self._confirmations.now() + timedelta(seconds=self.limits.preview_ttl_seconds),
            )
            prepared = PreparedOfficeRead(plan=plan, confirmation_id=confirmation_id)
            self._pending[plan.plan_id] = prepared
        self._audit.record("read.previewed", str(plan.plan_id), count=len(grants))
        return prepared

    def confirm_read(self, plan_id: UUID, approved: bool) -> None:
        """Resolve the exact pending R0 plan following a user decision."""
        prepared = self._pending_read(plan_id)
        self._validate_read(prepared.plan)
        self._confirmations.resolve(
            prepared.confirmation_id,
            prepared.plan.canonical_digest(),
            "READ",
            approved,
        )

    def read(
        self, plan_id: UUID, cancellation: CancellationToken | None = None
    ) -> tuple[OfficeReadResult, ...]:
        """Consume one plan, read selected files sequentially and audit truthful failure."""
        token = cancellation or CancellationToken()
        with self._lock:
            prepared = self._pending_read(plan_id)
            self._validate_read(prepared.plan)
            self._confirmations.consume(
                prepared.confirmation_id,
                prepared.plan.canonical_digest(),
                "READ",
            )
            self._active = prepared.plan
            results: list[OfficeReadResult] = []
            try:
                total = 0
                parsed_bytes = 0
                for grant_id in prepared.plan.grant_ids:
                    result = self.registry.execute(
                        "office.document.read",
                        OfficeReadRequest(plan_id=plan_id, grant_id=grant_id).model_dump(
                            mode="json"
                        ),
                        token,
                    )
                    if not isinstance(result, OfficeReadResult):
                        raise OfficeError("OFFICE_READ_RESULT_INVALID")
                    total += result.reference.identity.state.size_bytes
                    if total > self.limits.max_total_bytes:
                        raise OfficeError("DOCUMENT_TOTAL_SIZE_LIMIT")
                    parsed_bytes += len(result.document.model_dump_json().encode("utf-8"))
                    if parsed_bytes + self._cache_bytes > self.limits.parsed_cache_bytes:
                        raise OfficeError("DOCUMENT_PARSED_CACHE_LIMIT")
                    results.append(result)
                if len(self._results) + len(results) > 100:
                    raise OfficeError("OFFICE_RESULT_SESSION_LIMIT")
                self._results.update({item.reference.document_id: item for item in results})
                self._cache_bytes += parsed_bytes
                self._audit.record("read.verified", str(plan_id), count=len(results), size=total)
                return tuple(results)
            except OfficeError as exc:
                self._audit.record("read.failed", str(plan_id), code=exc.code)
                raise
            finally:
                self._active = None
                self._pending.pop(plan_id, None)

    def result(self, document_id: UUID) -> OfficeReadResult:
        """Return session-local data only; a report/reference is never write authority."""
        try:
            DocumentTargetResolver().resolve(document_id, tuple(self._results.values()))
            return self._results[document_id]
        except KeyError as exc:
            raise OfficeError("DOCUMENT_REFERENCE_MISSING") from exc

    def clear_results(self) -> None:
        """Drop volatile parsed content; no user file or backup is removed."""
        with self._lock:
            self._results.clear()
            self._cache_bytes = 0

    def revalidate(self, document_id: UUID) -> None:
        """Recheck a previously confirmed exact document before disclosing a cached span."""
        reference = self.result(document_id).reference
        grant = self.grants.get(reference.grant_id, OfficeGrantKind.READ)
        with self._files.pin_parents(grant.path), self._files.open(grant.path) as lease:
            data = lease.read(self.limits.other_bytes, CancellationToken())
            if not reference.identity.matches(lease.identity(data, grant.format)):
                raise OfficeError("DOCUMENT_IDENTITY_CHANGED")

    def _pending_read(self, plan_id: UUID) -> PreparedOfficeRead:
        try:
            return self._pending[plan_id]
        except KeyError as exc:
            raise OfficeError("OFFICE_READ_PLAN_MISSING") from exc

    def _validate_read(self, plan: OfficeReadPlan) -> None:
        current = tuple(
            self.grants.get(item, OfficeGrantKind.READ).canonical_digest()
            for item in plan.grant_ids
        )
        if current != plan.grant_digests or office_digest(self.limits) != plan.policy_digest:
            raise OfficeError("OFFICE_READ_PLAN_CHANGED")
        total = sum(
            self.grants.get(item, OfficeGrantKind.READ).path.stat().st_size
            for item in plan.grant_ids
        )
        if total > self.limits.max_total_bytes:
            raise OfficeError("DOCUMENT_TOTAL_SIZE_LIMIT")

    def _read_one(self, request: OfficeReadRequest, token: CancellationToken) -> OfficeReadResult:
        from pc_manager_agent.domain.office_documents import DocumentReference

        active = self._active
        if (
            active is None
            or active.plan_id != request.plan_id
            or request.grant_id not in active.grant_ids
        ):
            raise OfficeError("OFFICE_READ_OUTSIDE_CONFIRMED_PLAN")
        grant = self.grants.get(request.grant_id, OfficeGrantKind.READ)
        with self._files.pin_parents(grant.path), self._files.open(grant.path) as lease:
            data = lease.read(self.limits.other_bytes, token)
            identity = lease.identity(data, grant.format)
            document = self._parser.parse(data, grant.format, token, active.delimiter)
            if not identity.matches(lease.identity(data, grant.format)):
                raise OfficeError("DOCUMENT_CHANGED_DURING_READ")
            self.grants.get(request.grant_id, OfficeGrantKind.READ)
        return OfficeReadResult(
            reference=DocumentReference(grant_id=grant.grant_id, identity=identity),
            document=document,
        )
