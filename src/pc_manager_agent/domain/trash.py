"""Immutable Stage 2B models for explicit Windows Recycle Bin operations."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.file_operations import FileState, OperationType
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel


def _digest_model(value: FrozenModel) -> str:
    """Return a stable SHA-256 digest for one immutable Pydantic model."""
    payload = value.model_dump(mode="json")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class TrashPreviewStatus(StrEnum):
    """Whether one explicitly selected object may enter runtime confirmation."""

    READY = "READY"
    BLOCKED = "BLOCKED"


class TrashImpactLevel(StrEnum):
    """Additional user-facing batch impact without inventing a new risk class."""

    NORMAL = "NORMAL"
    HIGH = "HIGH"


class RecycleVerificationStatus(StrEnum):
    """What deterministic evidence exists after the Shell operation returns."""

    VERIFIED_RECYCLED = "VERIFIED_RECYCLED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class RecycleBinCapability(FrozenModel):
    """Preflight assessment for the volume that owns a selected object."""

    available: bool
    volume_root: Path | None = None
    filesystem: str | None = None
    volume_serial: int | None = Field(default=None, ge=0)
    fixed_drive: bool = False
    read_only: bool = False
    hotplug: bool | None = None
    recycle_bin_query_succeeded: bool = False
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_capability(self) -> Self:
        """Reject a contradictory capability that claims unsafe media is available."""
        if self.available and (
            self.volume_root is None
            or not self.fixed_drive
            or self.read_only
            or self.hotplug is not False
            or not self.recycle_bin_query_succeeded
            or (self.filesystem or "").casefold() != "ntfs"
        ):
            raise ValueError("Available Recycle Bin capability must be local fixed writable NTFS")
        if not self.available and not self.reason:
            raise ValueError("Unavailable Recycle Bin capability requires a reason")
        return self


class TrashPlanItem(FrozenModel):
    """One user-selected path with the identity observed while compiling the plan."""

    operation_id: UUID = Field(default_factory=uuid4)
    sequence: int = Field(ge=0)
    operation_type: OperationType
    source: Path
    expected_source_state: FileState
    tool_name: str = "file.trash"
    rollback_level: RollbackLevel = RollbackLevel.MANUAL

    @model_validator(mode="after")
    def validate_item(self) -> Self:
        """Allow only a matching file/directory Recycle Bin operation."""
        expected = (
            OperationType.RECYCLE_FILE
            if self.expected_source_state.kind.value == "FILE"
            else OperationType.RECYCLE_DIRECTORY
        )
        if self.operation_type is not expected:
            raise ValueError("Recycle operation type does not match the observed object kind")
        if self.source != self.expected_source_state.path:
            raise ValueError("Recycle source must match its identity observation")
        if self.tool_name != "file.trash" or self.rollback_level is not RollbackLevel.MANUAL:
            raise ValueError("Stage 2B items require file.trash with MANUAL recovery")
        return self


class TrashPlan(FrozenModel):
    """Complete deterministic R2 plan built only from an explicit UI selection."""

    plan_id: UUID = Field(default_factory=uuid4)
    plan_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: str = Field(min_length=1, max_length=500)
    user_goal: str = Field(min_length=1, max_length=2_000)
    authorized_root_ids: tuple[UUID, ...]
    items: tuple[TrashPlanItem, ...]
    risk_level: RiskLevel = RiskLevel.R2
    requires_plan_confirmation: bool = True
    requires_runtime_confirmation: bool = True
    rollback_level: RollbackLevel = RollbackLevel.MANUAL

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        """Enforce contiguous unique items and the fixed R2 confirmation contract."""
        if not self.authorized_root_ids or not self.items:
            raise ValueError("Trash plans require authorized roots and selected objects")
        if (
            self.risk_level is not RiskLevel.R2
            or not self.requires_plan_confirmation
            or not self.requires_runtime_confirmation
            or self.rollback_level is not RollbackLevel.MANUAL
        ):
            raise ValueError("Trash plans require R2, two confirmations, and MANUAL recovery")
        sequences = [item.sequence for item in self.items]
        if sequences != list(range(len(sequences))):
            raise ValueError("Trash item sequence must be contiguous and ordered")
        identifiers = [item.operation_id for item in self.items]
        sources = [str(item.source).casefold() for item in self.items]
        if len(identifiers) != len(set(identifiers)) or len(sources) != len(set(sources)):
            raise ValueError("Trash plan items and source paths must be unique")
        return self

    def canonical_digest(self) -> str:
        """Bind every execution-relevant plan field to confirmations and persistence."""
        return _digest_model(self)


class TrashObjectSnapshot(FrozenModel):
    """Bounded identity snapshot of a selected file or complete directory tree."""

    source: Path
    root_state: FileState
    tree_digest: str = Field(min_length=64, max_length=64)
    object_count: int = Field(ge=1)
    total_size_bytes: int = Field(ge=0)
    largest_item_bytes: int = Field(ge=0)
    hidden_count: int = Field(ge=0)
    system_count: int = Field(ge=0)
    reparse_count: int = Field(ge=0)
    offline_count: int = Field(ge=0)

    def canonical_digest(self) -> str:
        """Hash the complete snapshot used to detect selection changes."""
        return _digest_model(self)


class TrashPreviewIssue(FrozenModel):
    """Stable reason why an explicitly selected object is blocked."""

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    message: str = Field(min_length=1, max_length=500)


class TrashPreviewItem(FrozenModel):
    """Read-only preflight result for one selected path."""

    operation_id: UUID
    sequence: int = Field(ge=0)
    source: Path
    status: TrashPreviewStatus
    snapshot: TrashObjectSnapshot | None = None
    capability: RecycleBinCapability | None = None
    issues: tuple[TrashPreviewIssue, ...] = ()
    rollback_level: RollbackLevel = RollbackLevel.MANUAL

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        """Require evidence for READY and an explanation for BLOCKED."""
        if self.status is TrashPreviewStatus.READY:
            if self.snapshot is None or self.capability is None or not self.capability.available:
                raise ValueError("READY trash items require a safe snapshot and capability")
            if self.issues:
                raise ValueError("READY trash items cannot contain blocking issues")
        elif not self.issues:
            raise ValueError("BLOCKED trash items require at least one issue")
        return self


class TrashPreview(FrozenModel):
    """Exact R2 Preview shown before plan and runtime confirmation."""

    preview_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    items: tuple[TrashPreviewItem, ...]
    object_set_digest: str = Field(min_length=64, max_length=64)
    selected_count: int = Field(ge=1)
    ready_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    contained_object_count: int = Field(ge=0)
    total_size_bytes: int = Field(ge=0)
    largest_item_bytes: int = Field(ge=0)
    impact_level: TrashImpactLevel
    risk_level: RiskLevel = RiskLevel.R2
    rollback_level: RollbackLevel = RollbackLevel.MANUAL

    @model_validator(mode="after")
    def validate_totals(self) -> Self:
        """Ensure displayed totals are derived from the immutable item snapshot."""
        if self.selected_count != len(self.items):
            raise ValueError("Selected count does not match Preview items")
        ready = sum(item.status is TrashPreviewStatus.READY for item in self.items)
        blocked = sum(item.status is TrashPreviewStatus.BLOCKED for item in self.items)
        if ready != self.ready_count or blocked != self.blocked_count:
            raise ValueError("Trash Preview status totals do not match items")
        snapshots = tuple(item.snapshot for item in self.items if item.snapshot is not None)
        if self.contained_object_count != sum(item.object_count for item in snapshots):
            raise ValueError("Contained object total does not match snapshots")
        if self.total_size_bytes != sum(item.total_size_bytes for item in snapshots):
            raise ValueError("Trash Preview size total does not match snapshots")
        expected_largest = max((item.largest_item_bytes for item in snapshots), default=0)
        if self.largest_item_bytes != expected_largest:
            raise ValueError("Largest item size does not match snapshots")
        return self

    def canonical_digest(self) -> str:
        """Hash the full display snapshot for confirmation and journal binding."""
        return _digest_model(self)


class RecycleBinResult(FrozenModel):
    """Outcome returned by the Windows adapter for one queued Shell operation."""

    source: Path
    hresult: int
    aborted: bool
    recycled: bool
    recycle_item_identifier: str | None = Field(default=None, max_length=2_048)
    verification_status: RecycleVerificationStatus
    message: str = Field(min_length=1, max_length=1_000)


class TrashExecutionReport(FrozenModel):
    """Terminal R2 transaction summary safe to display after verification."""

    transaction_id: UUID
    plan_id: UUID
    completed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    total_size_bytes: int = Field(ge=0)
    recovery_level: RollbackLevel = RollbackLevel.MANUAL
    results: tuple[RecycleBinResult, ...]
