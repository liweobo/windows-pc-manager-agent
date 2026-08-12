"""Immutable manual-recovery records for Stage 2B Recycle Bin operations."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.file_operations import FileState
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.domain.trash import RecycleVerificationStatus


class RecoveryStatus(StrEnum):
    """Lifecycle of evidence for a manually recoverable Recycle Bin item."""

    PREPARED = "PREPARED"
    AVAILABLE = "AVAILABLE"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class TrashRecoveryRecord(FrozenModel):
    """Durable evidence and instructions; it never promises automatic restoration."""

    recovery_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID
    operation_id: UUID
    sequence: int = Field(ge=0)
    original_path: Path
    before_state: FileState
    status: RecoveryStatus = RecoveryStatus.PREPARED
    recovery_level: RollbackLevel = RollbackLevel.MANUAL
    prepared_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    recycled_at: datetime | None = None
    recycle_item_identifier: str | None = Field(default=None, max_length=2_048)
    verification_status: RecycleVerificationStatus | None = None
    result_message: str | None = Field(default=None, max_length=1_000)
    instructions: str = (
        "Open Windows Recycle Bin, locate the item by its original name and deletion time, "
        "then choose Restore. Automatic restoration is not available."
    )

    def canonical_digest(self) -> str:
        """Detect corruption of paths, identities, outcome, or recovery claims."""
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
