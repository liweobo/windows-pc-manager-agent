"""Office-only journal and backup records; document bodies never enter audit tables."""

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.office_documents import DocumentFormat, OfficeDocumentIdentity
from pc_manager_agent.domain.office_plans import DocumentOperationKind, OutputMode
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel


class OfficeTransactionState(StrEnum):
    """Interrupted work never resumes or automatically commits."""

    PREVIEWED = "PREVIEWED"
    CONFIRMED = "CONFIRMED"
    WRITING_TEMP = "WRITING_TEMP"
    VERIFYING_TEMP = "VERIFYING_TEMP"
    COMMITTING = "COMMITTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"
    RESTORED = "RESTORED"


class OfficeDocumentBackup(FrozenModel):
    """Verified encrypted binary backup with immutable original identity."""

    backup_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID
    source: OfficeDocumentIdentity
    path: Path
    cipher_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    created_at: datetime
    rollback_level: RollbackLevel = RollbackLevel.FULL


class OfficeTransaction(FrozenModel):
    """Durable metadata only; edit values and Diff remain volatile."""

    transaction_id: UUID
    plan_id: UUID
    preview_id: UUID
    plan_digest: str
    preview_digest: str
    output_path: Path
    mode: OutputMode
    risk_level: RiskLevel
    format: DocumentFormat | None = None
    operation_kinds: tuple[DocumentOperationKind, ...] = ()
    input_identity_digests: tuple[str, ...] = ()
    confirmation_ids: tuple[UUID, ...] = ()
    state: OfficeTransactionState = OfficeTransactionState.PREVIEWED
    source: OfficeDocumentIdentity | None = None
    backup_id: UUID | None = None
    result: OfficeDocumentIdentity | None = None
    recovery_of: UUID | None = None
    retained_original_path: Path | None = None
    temporary_path: Path | None = None
    error_code: str | None = None
    created_at: datetime
    expires_at: datetime
