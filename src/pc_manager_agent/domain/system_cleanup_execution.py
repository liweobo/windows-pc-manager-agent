"""Immutable Stage 4E2 models for narrowly controlled cleanup execution."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.file_operations import FileState
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.software_uninstall_analysis import canonical_digest
from pc_manager_agent.domain.system_optimization import (
    CleanupCategory,
    CleanupSafetyClassification,
    OptimizationConfidence,
    ProtectionLevel,
)
from pc_manager_agent.domain.trash import (
    RecycleBinCapability,
    RecycleBinResult,
    TrashObjectSnapshot,
)


class CleanupEligibilityDecision(StrEnum):
    """Finite local decision; only ELIGIBLE can enter the Stage 4E2 writer."""

    ELIGIBLE = "ELIGIBLE"
    BLOCKED = "BLOCKED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    DEFERRED = "DEFERRED"


class CleanupAdapterType(StrEnum):
    """Exact execution or hand-off routes; no generic delete route exists."""

    RECYCLE_BIN_ITEM = "RECYCLE_BIN_ITEM"
    RECYCLE_BIN_EMPTY = "RECYCLE_BIN_EMPTY"
    STAGE4D4_HANDOFF = "STAGE4D4_HANDOFF"
    STAGE2B_HANDOFF = "STAGE2B_HANDOFF"
    WINDOWS_SUPPORTED_MAINTENANCE = "WINDOWS_SUPPORTED_MAINTENANCE"
    DEFERRED = "DEFERRED"


class CleanupAction(StrEnum):
    """Representable Stage 4E2 actions; DELETE is intentionally absent."""

    MOVE_TO_RECYCLE_BIN = "MOVE_TO_RECYCLE_BIN"
    EMPTY_RECYCLE_BIN = "EMPTY_RECYCLE_BIN"


class CleanupRecoveryLevel(StrEnum):
    """Truthful user-facing recovery capability."""

    FULL = "FULL"
    PARTIAL = "PARTIAL"
    MANUAL = "MANUAL"
    UNKNOWN = "UNKNOWN"
    NONE = "NONE"


class CleanupTransactionState(StrEnum):
    """Durable lifecycle that never automatically resumes interrupted work."""

    PREVIEWED = "PREVIEWED"
    AWAITING_PLAN_CONFIRMATION = "AWAITING_PLAN_CONFIRMATION"
    PLAN_CONFIRMED = "PLAN_CONFIRMED"
    AWAITING_RUNTIME_CONFIRMATION = "AWAITING_RUNTIME_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    VALIDATING = "VALIDATING"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"
    BLOCKED = "BLOCKED"


class CleanupItemState(StrEnum):
    """Per-item state retained even when a fail-stop batch is partial."""

    PENDING = "PENDING"
    VALIDATING = "VALIDATING"
    IDENTITY_CHANGED = "IDENTITY_CHANGED"
    TRASHING = "TRASHING"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    BLOCKED_CHANGED = "BLOCKED_CHANGED"
    SKIPPED = "SKIPPED"


class CleanupVerificationStatus(StrEnum):
    """Identity-aware interpretation of one cleanup result."""

    ORIGINAL_OBJECT_REMOVED = "ORIGINAL_OBJECT_REMOVED"
    ORIGINAL_REMOVED_NEW_OBJECT_PRESENT = "ORIGINAL_REMOVED_NEW_OBJECT_PRESENT"
    ORIGINAL_OBJECT_STILL_PRESENT = "ORIGINAL_OBJECT_STILL_PRESENT"
    RECYCLE_BIN_EMPTY_VERIFIED = "RECYCLE_BIN_EMPTY_VERIFIED"
    UNKNOWN = "UNKNOWN"


class SystemCleanupRequest(FrozenModel):
    """Stage 4E1 intent containing only a local report ID and explicit candidate IDs."""

    request_id: UUID = Field(default_factory=uuid4)
    source_report_id: UUID
    selected_candidate_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_unique_selection(self) -> Self:
        """Reject duplicates so selection and later totals remain exact."""
        if len(self.selected_candidate_ids) != len(set(self.selected_candidate_ids)):
            raise ValueError("Cleanup candidate selection IDs must be unique")
        return self


class CleanupObjectIdentity(FrozenModel):
    """Handle-derived identity and exact volume/path information for one fresh object."""

    resolved_path: Path
    state: FileState
    volume_root: Path
    reparse_point: bool = False

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        """Eligible identities must describe one exact non-reparse absolute path."""
        if not self.resolved_path.is_absolute() or not self.volume_root.is_absolute():
            raise ValueError("Cleanup identity paths must be absolute")
        if self.resolved_path != self.state.path:
            raise ValueError("Cleanup identity path and FileState path differ")
        if self.reparse_point:
            raise ValueError("Reparse points cannot become cleanup identities")
        return self

    def canonical_digest(self) -> str:
        """Hash the path, volume and File ID used for TOCTOU validation."""
        return canonical_digest(self.model_dump(mode="json"))


class CleanupMaterialSnapshot(FrozenModel):
    """Complete bounded tree evidence for classification and material-change checks."""

    tree: TrashObjectSnapshot
    file_count: int = Field(ge=0)
    directory_count: int = Field(ge=0)
    recently_modified_count: int = Field(ge=0)
    hidden_count: int = Field(ge=0)
    reparse_count: int = Field(ge=0)
    sensitive_signal_count: int = Field(ge=0)
    classification_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        """Require counts to describe the same complete tree as the shared snapshot."""
        if self.file_count + self.directory_count != self.tree.object_count:
            raise ValueError("Cleanup material counts do not match the tree snapshot")
        if self.hidden_count != self.tree.hidden_count:
            raise ValueError("Cleanup hidden count does not match the tree snapshot")
        if self.reparse_count != self.tree.reparse_count:
            raise ValueError("Cleanup reparse count does not match the tree snapshot")
        return self

    def canonical_digest(self) -> str:
        """Hash the exact material evidence displayed and confirmed."""
        return canonical_digest(self.model_dump(mode="json"))


class CleanupPathSafetyDecision(FrozenModel):
    """Path and scope evidence independent from classification and ownership."""

    safe: bool
    current_user_scope: bool
    exact_known_root: bool
    other_user: bool
    shared_location: bool
    sensitive_path: bool
    reparse_detected: bool
    network_or_unsupported_volume: bool
    reason_codes: tuple[str, ...] = Field(min_length=1)

    def canonical_digest(self) -> str:
        """Bind every path-safety signal to confirmation."""
        return canonical_digest(self.model_dump(mode="json"))


class CleanupActivityDecision(FrozenModel):
    """Fresh recent-write, lock and installer-activity evidence."""

    recently_modified: bool
    delete_access_available: bool
    active_installer_detected: bool
    blocked: bool
    reason_codes: tuple[str, ...] = Field(min_length=1)

    def canonical_digest(self) -> str:
        """Bind activity and lock evidence to the exact plan."""
        return canonical_digest(self.model_dump(mode="json"))


class CleanupRecoverabilityDecision(FrozenModel):
    """Exact Recycle Bin capability and truthful recovery claim."""

    capability: RecycleBinCapability
    recovery_level: CleanupRecoveryLevel = CleanupRecoveryLevel.MANUAL
    reason_codes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_recovery(self) -> Self:
        """Ordinary Stage 4E2 cleanup can claim only MANUAL Recycle Bin recovery."""
        if self.recovery_level is not CleanupRecoveryLevel.MANUAL:
            raise ValueError("Recycle Bin item cleanup recovery must be MANUAL")
        if not self.capability.available:
            raise ValueError("Recoverable item cleanup requires available Recycle Bin capability")
        return self

    def canonical_digest(self) -> str:
        """Hash recovery capability independently from identity and classification."""
        return canonical_digest(self.model_dump(mode="json"))


class CleanupExecutionCandidate(FrozenModel):
    """Fresh item-level decision; a Stage 4E1 candidate can never substitute for it."""

    item_ref: UUID = Field(default_factory=uuid4)
    source_report_id: UUID
    source_candidate_id: UUID
    path: Path | None = None
    fresh_identity: CleanupObjectIdentity | None = None
    material: CleanupMaterialSnapshot | None = None
    category: CleanupCategory
    source: str = Field(min_length=1, max_length=200)
    safety_classification: CleanupSafetyClassification
    protection_level: ProtectionLevel
    analysis_confidence: OptimizationConfidence
    eligibility: CleanupEligibilityDecision
    observed_size_bytes: int | None = Field(default=None, ge=0)
    item_count: int | None = Field(default=None, ge=0)
    recoverability: CleanupRecoveryLevel
    path_safety: CleanupPathSafetyDecision | None = None
    activity: CleanupActivityDecision | None = None
    recoverability_evidence: CleanupRecoverabilityDecision | None = None
    risk_flags: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = Field(min_length=1)
    cleanup_adapter_type: CleanupAdapterType
    upstream_report_id: UUID | None = None
    upstream_candidate_id: UUID | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_execution_evidence(self) -> Self:
        """Require full Fresh evidence only for directly executable Recycle Bin items."""
        direct = self.cleanup_adapter_type is CleanupAdapterType.RECYCLE_BIN_ITEM
        if self.eligibility is CleanupEligibilityDecision.ELIGIBLE:
            if not direct:
                raise ValueError("Only direct Recycle Bin candidates may be ELIGIBLE")
            if (
                self.path is None
                or self.fresh_identity is None
                or self.material is None
                or self.path_safety is None
                or self.activity is None
                or self.recoverability_evidence is None
            ):
                raise ValueError("Eligible cleanup candidate lacks complete Fresh evidence")
            if (
                not self.path_safety.safe
                or self.activity.blocked
                or self.protection_level is not ProtectionLevel.NONE
                or self.analysis_confidence is not OptimizationConfidence.HIGH
                or self.recoverability is not CleanupRecoveryLevel.MANUAL
            ):
                raise ValueError("Eligible cleanup candidate contradicts safety evidence")
        if self.cleanup_adapter_type is CleanupAdapterType.STAGE4D4_HANDOFF and (
            self.upstream_report_id is None or self.upstream_candidate_id is None
        ):
            raise ValueError("Stage 4D4 hand-off requires exact upstream UUIDs")
        return self

    def invariant_digest(self) -> str:
        """Hash execution-relevant evidence while excluding transient generation time."""
        return canonical_digest(self.model_dump(mode="json", exclude={"generated_at"}))


class SystemCleanupAssessment(FrozenModel):
    """Fresh discovery retaining every eligible, blocked, review and deferred row."""

    assessment_id: UUID = Field(default_factory=uuid4)
    request: SystemCleanupRequest
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    items: tuple[CleanupExecutionCandidate, ...]
    eligible_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    manual_review_count: int = Field(ge=0)
    deferred_count: int = Field(ge=0)
    truncated: bool = False

    @model_validator(mode="after")
    def validate_totals(self) -> Self:
        """Keep mixed-batch status visible instead of dropping blocked discoveries."""
        expected = {
            CleanupEligibilityDecision.ELIGIBLE: self.eligible_count,
            CleanupEligibilityDecision.BLOCKED: self.blocked_count,
            CleanupEligibilityDecision.MANUAL_REVIEW: self.manual_review_count,
            CleanupEligibilityDecision.DEFERRED: self.deferred_count,
        }
        for decision, count in expected.items():
            if sum(item.eligibility is decision for item in self.items) != count:
                raise ValueError(f"Cleanup assessment {decision.value} count does not match")
        return self

    def invariant_digest(self) -> str:
        """Hash the request and complete item evidence without transient timestamps."""
        return canonical_digest(
            self.model_dump(
                mode="json",
                exclude={
                    "assessment_id": True,
                    "generated_at": True,
                    "items": {"__all__": {"generated_at"}},
                },
            )
        )


class PlannedCleanupItem(FrozenModel):
    """One exact eligible item whose action is fixed to Recycle Bin placement."""

    operation_id: UUID = Field(default_factory=uuid4)
    sequence: int = Field(ge=0)
    candidate: CleanupExecutionCandidate
    action: CleanupAction = CleanupAction.MOVE_TO_RECYCLE_BIN
    tool_name: str = "optimization.cleanup.trash"
    rollback_level: RollbackLevel = RollbackLevel.MANUAL

    @model_validator(mode="after")
    def require_eligible_candidate(self) -> Self:
        """Prevent hand-offs, blocked rows and generic tools from entering execution."""
        if self.candidate.eligibility is not CleanupEligibilityDecision.ELIGIBLE:
            raise ValueError("Only freshly eligible cleanup candidates may be planned")
        if self.candidate.cleanup_adapter_type is not CleanupAdapterType.RECYCLE_BIN_ITEM:
            raise ValueError("Stage 4E2 direct plans require RECYCLE_BIN_ITEM")
        if self.tool_name != "optimization.cleanup.trash":
            raise ValueError("Stage 4E2 item plans require the dedicated trash tool")
        if self.rollback_level is not RollbackLevel.MANUAL:
            raise ValueError("Stage 4E2 item cleanup recovery is MANUAL")
        return self

    def canonical_digest(self) -> str:
        """Hash exact candidate, action, tool and recovery semantics."""
        return canonical_digest(self.model_dump(mode="json"))


class CleanupRecoverySummary(FrozenModel):
    """Aggregate recovery truth displayed before execution."""

    level: CleanupRecoveryLevel
    manual_items: int = Field(ge=0)
    none_items: int = Field(ge=0)
    automatic_restore_available: bool = False


class CleanupExecutionPlan(FrozenModel):
    """Executable all-eligible batch compiled from exact Fresh item selections."""

    plan_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID = Field(default_factory=uuid4)
    request_id: UUID
    source_report_id: UUID
    assessment_id: UUID
    assessment_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: tuple[PlannedCleanupItem, ...] = Field(min_length=1, max_length=100)
    total_items: int = Field(ge=1)
    contained_object_count: int = Field(ge=1)
    total_observed_bytes: int = Field(ge=0)
    largest_item_bytes: int = Field(ge=0)
    risk_level: RiskLevel
    recovery_summary: CleanupRecoverySummary
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    requires_plan_confirmation: bool = True
    requires_runtime_confirmation: bool = True
    rollback_level: RollbackLevel = RollbackLevel.MANUAL

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        """Enforce exact totals, order, risk, two confirmations and MANUAL recovery."""
        if self.expires_at <= self.created_at:
            raise ValueError("Cleanup plan expiry must follow creation")
        if self.risk_level not in {RiskLevel.R2, RiskLevel.R2_HIGH_IMPACT}:
            raise ValueError("Cleanup plans must be R2 or R2_HIGH_IMPACT")
        if not self.requires_plan_confirmation or not self.requires_runtime_confirmation:
            raise ValueError("Cleanup plans require two confirmations")
        if self.rollback_level is not RollbackLevel.MANUAL:
            raise ValueError("Cleanup plans use MANUAL Recycle Bin recovery")
        if self.total_items != len(self.items):
            raise ValueError("Cleanup plan item count does not match")
        if [item.sequence for item in self.items] != list(range(len(self.items))):
            raise ValueError("Cleanup plan item sequence must be contiguous")
        paths = [str(item.candidate.path).casefold() for item in self.items]
        if len(paths) != len(set(paths)):
            raise ValueError("Cleanup plan paths must be unique")
        materials = tuple(item.candidate.material for item in self.items)
        if any(material is None for material in materials):
            raise ValueError("Cleanup plan items require material snapshots")
        if self.contained_object_count != sum(
            material.tree.object_count for material in materials if material is not None
        ):
            raise ValueError("Cleanup plan contained-object count does not match")
        if self.total_observed_bytes != sum(
            material.tree.total_size_bytes for material in materials if material is not None
        ):
            raise ValueError("Cleanup plan byte total does not match")
        return self

    def canonical_digest(self) -> str:
        """Hash every field used by persistence, confirmation and execution."""
        return canonical_digest(self.model_dump(mode="json"))


class CleanupExecutionPreview(FrozenModel):
    """Exact user-visible Preview for an ordinary Stage 4E2 batch."""

    preview_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID
    plan_id: UUID
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    items: tuple[PlannedCleanupItem, ...]
    item_set_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    total_items: int = Field(ge=1)
    contained_object_count: int = Field(ge=1)
    total_observed_bytes: int = Field(ge=0)
    largest_item_bytes: int = Field(ge=0)
    risk_level: RiskLevel
    recovery_summary: CleanupRecoverySummary
    permanent_delete_available: bool = False

    @model_validator(mode="after")
    def validate_preview(self) -> Self:
        """Prevent stale totals or permanent-delete claims from reaching confirmation."""
        if self.expires_at <= self.generated_at:
            raise ValueError("Cleanup Preview expiry must follow generation")
        if self.total_items != len(self.items):
            raise ValueError("Cleanup Preview item count does not match")
        if self.permanent_delete_available:
            raise ValueError("Stage 4E2 has no permanent-delete option")
        return self

    def canonical_digest(self) -> str:
        """Hash the exact content shown in both confirmation dialogs."""
        return canonical_digest(self.model_dump(mode="json"))


class SystemCleanupTrashRequest(FrozenModel):
    """Reference-only write request resolved from durable local state."""

    transaction_id: UUID
    cleanup_plan_id: UUID
    preview_id: UUID
    validated_item_ref: UUID


class SystemCleanupTrashResult(FrozenModel):
    """Low-level Shell result returned before independent identity verification."""

    item_ref: UUID
    original_identity: FileState
    outcome: RecycleBinResult


class CleanupItemResult(FrozenModel):
    """Terminal result for one exact selected cleanup object."""

    item_ref: UUID
    operation_id: UUID
    sequence: int = Field(ge=0)
    source_candidate_id: UUID
    path: Path
    state: CleanupItemState
    verification_status: CleanupVerificationStatus
    recycle_result: RecycleBinResult | None = None
    recovery_id: UUID | None = None
    message: str = Field(min_length=1, max_length=1_000)


class CleanupResult(FrozenModel):
    """Truthful batch result separating moved bytes from reclaimed disk space."""

    transaction_id: UUID
    plan_id: UUID
    verified_items: int = Field(ge=0)
    failed_items: int = Field(ge=0)
    blocked_items: int = Field(ge=0)
    skipped_items: int = Field(ge=0)
    bytes_removed_from_original_locations: int = Field(ge=0)
    verified_disk_space_reclaimed_bytes: int | None = Field(default=None, ge=0)
    observed_free_space_change_bytes: int | None = None
    recovery_records: tuple[UUID, ...]
    final_state: CleanupTransactionState
    results: tuple[CleanupItemResult, ...]


class RecycleBinInventorySnapshot(FrozenModel):
    """Fresh aggregate and bounded Shell-namespace evidence for one exact volume."""

    volume_root: Path
    item_count: int = Field(ge=0)
    observed_size_bytes: int = Field(ge=0)
    oldest_deleted_at: datetime | None = None
    newest_deleted_at: datetime | None = None
    enumeration_complete: bool
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        """Non-empty executable snapshots require complete deletion-time evidence."""
        if not self.volume_root.is_absolute():
            raise ValueError("Recycle Bin scope must be one absolute volume root")
        if (
            self.item_count
            and self.enumeration_complete
            and (self.oldest_deleted_at is None or self.newest_deleted_at is None)
        ):
            raise ValueError("Complete non-empty Recycle Bin evidence requires age bounds")
        return self

    def canonical_digest(self) -> str:
        """Hash exact scope, totals and age evidence while excluding collection time."""
        return canonical_digest(self.model_dump(mode="json", exclude={"collected_at"}))


class RecycleBinInventoryRequest(FrozenModel):
    """Path-free request for the fixed current-user system-volume Recycle Bin scope."""

    scope: str = Field(default="CURRENT_USER_SYSTEM_VOLUME", pattern="^CURRENT_USER_SYSTEM_VOLUME$")


class RecycleBinEmptyPlan(FrozenModel):
    """Independent irreversible plan for one exact volume Recycle Bin scope."""

    plan_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID = Field(default_factory=uuid4)
    operation_id: UUID = Field(default_factory=uuid4)
    snapshot: RecycleBinInventorySnapshot
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    risk_level: RiskLevel = RiskLevel.R2_HIGH_IMPACT
    recovery_level: CleanupRecoveryLevel = CleanupRecoveryLevel.NONE
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    requires_plan_confirmation: bool = True
    requires_runtime_confirmation: bool = True

    @model_validator(mode="after")
    def validate_empty_plan(self) -> Self:
        """Require complete, non-empty, exact-scope evidence and no recovery claim."""
        if self.expires_at <= self.created_at:
            raise ValueError("Recycle Bin empty plan expiry must follow creation")
        if not self.snapshot.enumeration_complete or self.snapshot.item_count == 0:
            raise ValueError("Recycle Bin emptying requires a complete non-empty snapshot")
        if self.snapshot_digest != self.snapshot.canonical_digest():
            raise ValueError("Recycle Bin snapshot digest does not match")
        if self.risk_level is not RiskLevel.R2_HIGH_IMPACT:
            raise ValueError("Recycle Bin emptying is always R2_HIGH_IMPACT")
        if self.recovery_level is not CleanupRecoveryLevel.NONE:
            raise ValueError("Recycle Bin emptying has no Agent recovery")
        return self

    def canonical_digest(self) -> str:
        """Hash the independent irreversible plan."""
        return canonical_digest(self.model_dump(mode="json"))


class RecycleBinEmptyPreview(FrozenModel):
    """User-visible irreversible Preview that cannot join an ordinary cleanup batch."""

    preview_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID
    plan_id: UUID
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot: RecycleBinInventorySnapshot
    risk_level: RiskLevel = RiskLevel.R2_HIGH_IMPACT
    recovery_level: CleanupRecoveryLevel = CleanupRecoveryLevel.NONE
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    independent_confirmation_required: bool = True

    def canonical_digest(self) -> str:
        """Hash all scope, impact and irreversibility fields."""
        return canonical_digest(self.model_dump(mode="json"))


class RecycleBinEmptyRequest(FrozenModel):
    """Reference-only request for one durably confirmed empty transaction."""

    transaction_id: UUID
    plan_id: UUID
    preview_id: UUID


class RecycleBinEmptyResult(FrozenModel):
    """Verified or uncertain result of one specified-volume Shell empty call."""

    transaction_id: UUID
    volume_root: Path
    hresult: int
    verification_status: CleanupVerificationStatus
    before: RecycleBinInventorySnapshot
    after: RecycleBinInventorySnapshot | None = None
    recovery_level: CleanupRecoveryLevel = CleanupRecoveryLevel.NONE
    message: str = Field(min_length=1, max_length=1_000)


class CleanupIrreversibilityRecord(FrozenModel):
    """Durable truth for a Recycle Bin empty action; this is not a recovery record."""

    record_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID
    volume_root: Path
    item_count: int = Field(ge=1)
    observed_size_bytes: int = Field(ge=0)
    performed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    recovery_level: CleanupRecoveryLevel = CleanupRecoveryLevel.NONE
    message: str = "Agent cannot restore items after Recycle Bin emptying."
