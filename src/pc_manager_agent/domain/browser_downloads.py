"""Strict download Preview, identity, and recovery records for Stage 5C."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import Field

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel


class BrowserDownloadPreview(FrozenModel):
    """Exact one-file R1 Preview created before download authority is issued."""

    preview_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    page_id: UUID
    navigation_id: UUID
    element_id: UUID
    source_url: str = Field(min_length=1, max_length=4_096)
    source_origin: str = Field(min_length=1, max_length=512)
    suggested_filename: str = Field(min_length=1, max_length=255)
    declared_mime_type: str = Field(min_length=1, max_length=255)
    declared_size_bytes: int | None = Field(default=None, ge=0)
    destination: Path
    max_size_bytes: int = Field(gt=0)
    risk_level: RiskLevel = RiskLevel.R1
    rollback_level: RollbackLevel = RollbackLevel.FULL
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def canonical_digest(self) -> str:
        """Bind all identity, source, size, type, and destination fields."""
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class BrowserDownloadIdentity(FrozenModel):
    """Post-download evidence for one validated staged document."""

    download_id: UUID = Field(default_factory=uuid4)
    preview_id: UUID
    path: Path
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    detected_mime_type: str = Field(min_length=1, max_length=255)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BrowserDownloadRecoveryRecord(FrozenModel):
    """Conditional FULL recovery record for an unchanged staged download."""

    operation_id: UUID = Field(default_factory=uuid4)
    download: BrowserDownloadIdentity
    recovery_path: Path
    rollback_level: RollbackLevel = RollbackLevel.FULL
    valid_only_if_unchanged: bool = True


class BrowserWorkerDownload(FrozenModel):
    """Temporary artifact returned by the isolated worker before local validation."""

    session_id: UUID
    action_id: UUID
    source_url: str = Field(min_length=1, max_length=4_096)
    suggested_filename: str = Field(min_length=1, max_length=255)
    temporary_path: Path
