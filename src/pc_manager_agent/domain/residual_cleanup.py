"""Immutable Stage 4D4 models for freshly validated residual cleanup."""

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
from pc_manager_agent.domain.software_residuals import (
    OwnershipConfidence,
    OwnershipEvidence,
    ResidualClassification,
    ResidualSource,
    UserDataProtectionLevel,
)
from pc_manager_agent.domain.software_uninstall_analysis import canonical_digest
from pc_manager_agent.domain.trash import (
    RecycleBinCapability,
    RecycleBinResult,
    TrashObjectSnapshot,
)


class CleanupEligibilityDecision(StrEnum):
    """Whether fresh deterministic evidence permits controlled cleanup."""

    ELIGIBLE = "ELIGIBLE"
    BLOCKED = "BLOCKED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class ResidualCleanupAction(StrEnum):
    """The sole Stage 4D4 mutation; permanent deletion is unrepresentable."""

    MOVE_TO_RECYCLE_BIN = "MOVE_TO_RECYCLE_BIN"


class ResidualCleanupTransactionState(StrEnum):
    """Durable lifecycle of one non-resumable cleanup transaction."""

    PREVIEWED = "PREVIEWED"
    AWAITING_PLAN_CONFIRMATION = "AWAITING_PLAN_CONFIRMATION"
    PLAN_CONFIRMED = "PLAN_CONFIRMED"
    AWAITING_RUNTIME_CONFIRMATION = "AWAITING_RUNTIME_CONFIRMATION"
    DISPATCHING = "DISPATCHING"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"
    BLOCKED = "BLOCKED"


class ResidualCleanupItemState(StrEnum):
    """Per-item state used to report partial work without hiding skipped items."""

    PLANNED = "PLANNED"
    VALIDATING = "VALIDATING"
    TRASHING = "TRASHING"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    BLOCKED_CHANGED = "BLOCKED_CHANGED"
    SKIPPED = "SKIPPED"


class ResidualVerificationStatus(StrEnum):
    """Identity-aware interpretation of the Windows Recycle Bin result."""

    ORIGINAL_IDENTITY_REMOVED = "ORIGINAL_IDENTITY_REMOVED"
    ORIGINAL_REMOVED_NEW_OBJECT_PRESENT = "ORIGINAL_REMOVED_NEW_OBJECT_PRESENT"
    ORIGINAL_IDENTITY_STILL_PRESENT = "ORIGINAL_IDENTITY_STILL_PRESENT"
    UNKNOWN = "UNKNOWN"


class ResidualCleanupRequest(FrozenModel):
    """User intent referencing one report and explicitly selected candidate IDs only."""

    request_id: UUID = Field(default_factory=uuid4)
    source_report_id: UUID
    selected_residual_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_unique_selection(self) -> Self:
        """Reject duplicate IDs so one object cannot be represented twice."""
        if len(self.selected_residual_ids) != len(set(self.selected_residual_ids)):
            raise ValueError("Residual cleanup selection IDs must be unique")
        return self


class ResidualClassificationCount(FrozenModel):
    """Privacy-minimized count for classifications found inside one fresh tree."""

    classification: ResidualClassification
    count: int = Field(ge=1)


class ResidualProtectionCount(FrozenModel):
    """Count of protection levels found inside one fresh tree."""

    protection: UserDataProtectionLevel
    count: int = Field(ge=1)


class ResidualMaterialSnapshot(FrozenModel):
    """Complete bounded metadata snapshot used for material-change detection."""

    tree: TrashObjectSnapshot
    file_count: int = Field(ge=0)
    directory_count: int = Field(ge=0)
    minimum_modified_ns: int = Field(ge=0)
    maximum_modified_ns: int = Field(ge=0)
    classification_counts: tuple[ResidualClassificationCount, ...]
    protection_counts: tuple[ResidualProtectionCount, ...]
    forbidden_descendant_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        """Ensure material totals describe the exact Stage 2B tree snapshot."""
        if self.file_count + self.directory_count != self.tree.object_count:
            raise ValueError("Residual material counts do not match the tree snapshot")
        if self.minimum_modified_ns > self.maximum_modified_ns:
            raise ValueError("Residual modification range is inverted")
        if sum(item.count for item in self.classification_counts) != self.tree.object_count:
            raise ValueError("Residual classification counts do not match the tree")
        if sum(item.count for item in self.protection_counts) != self.tree.object_count:
            raise ValueError("Residual protection counts do not match the tree")
        return self

    def canonical_digest(self) -> str:
        """Hash identities, counts, times, classifications, and protections."""
        return canonical_digest(self.model_dump(mode="json"))


class ResidualPathSafetyDecision(FrozenModel):
    """Exact-path decision kept separate from ownership and classification."""

    safe: bool
    exact_context_path: bool
    ordinary_user_access: bool
    shared_location: bool
    reparse_detected: bool
    network_or_unsupported_volume: bool
    reason_codes: tuple[str, ...]

    def canonical_digest(self) -> str:
        """Bind every path-safety gate to plan confirmation."""
        return canonical_digest(self.model_dump(mode="json"))


class ResidualRecentActivityDecision(FrozenModel):
    """Whether fresh metadata shows writes after uninstall completion."""

    blocked: bool
    uninstall_completed_at: datetime
    newest_modified_at: datetime
    reason_codes: tuple[str, ...]

    def canonical_digest(self) -> str:
        """Bind post-uninstall activity evidence to confirmation."""
        return canonical_digest(self.model_dump(mode="json"))


class ResidualRecoverabilityDecision(FrozenModel):
    """Truthful recovery capability for one exact candidate and volume."""

    capability: RecycleBinCapability
    rollback_level: RollbackLevel = RollbackLevel.MANUAL
    reason_codes: tuple[str, ...]

    @model_validator(mode="after")
    def require_manual_recycle_bin(self) -> Self:
        """Prevent Stage 4D4 from claiming automatic FULL rollback."""
        if self.rollback_level is not RollbackLevel.MANUAL:
            raise ValueError("Stage 4D4 recovery is MANUAL")
        return self

    def canonical_digest(self) -> str:
        """Hash the volume capability and truthful recovery claim."""
        return canonical_digest(self.model_dump(mode="json"))


class FreshResidualCandidate(FrozenModel):
    """Freshly observed candidate; old Stage 4D3 metadata grants no authority."""

    source_report_id: UUID
    source_candidate_id: UUID
    context_id: UUID
    uninstall_transaction_id: UUID
    software_identity_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    path: Path
    scan_root: Path
    source: ResidualSource
    evidence_code: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    expected_old_identity_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    fresh_identity: FileState | None = None
    material: ResidualMaterialSnapshot | None = None
    classification: ResidualClassification
    classification_reasons: tuple[str, ...]
    ownership_confidence: OwnershipConfidence
    ownership_evidence: tuple[OwnershipEvidence, ...]
    protection_level: UserDataProtectionLevel
    protection_reasons: tuple[str, ...]
    path_safety: ResidualPathSafetyDecision | None = None
    recent_activity: ResidualRecentActivityDecision | None = None
    recoverability: ResidualRecoverabilityDecision | None = None
    eligibility: CleanupEligibilityDecision
    eligibility_reason_codes: tuple[str, ...]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_authority(self) -> Self:
        """Require coherent executable evidence and exact snapshot identity."""
        eligible = self.eligibility is CleanupEligibilityDecision.ELIGIBLE
        if eligible:
            if (
                self.fresh_identity is None
                or self.material is None
                or self.path_safety is None
                or self.recent_activity is None
                or self.recoverability is None
            ):
                raise ValueError("Eligible residual requires complete fresh evidence")
            if self.path != self.fresh_identity.path or self.path != self.material.tree.source:
                raise ValueError("Fresh residual path and identity evidence must match")
            if (
                self.ownership_confidence is not OwnershipConfidence.HIGH
                or self.protection_level
                not in {UserDataProtectionLevel.NONE, UserDataProtectionLevel.CAUTION}
                or not self.path_safety.safe
                or self.recent_activity.blocked
                or not self.recoverability.capability.available
                or self.material.forbidden_descendant_count
            ):
                raise ValueError("Eligible residual lacks complete fresh safety evidence")
        if not self.eligibility_reason_codes:
            raise ValueError("Residual eligibility requires reason codes")
        return self

    def invariant_digest(self) -> str:
        """Hash all execution-relevant evidence while excluding generation time."""
        payload = self.model_dump(mode="json", exclude={"generated_at"})
        return canonical_digest(payload)


class ResidualCleanupAssessment(FrozenModel):
    """Fresh R0 assessment retaining every selected row, including blocked rows."""

    assessment_id: UUID = Field(default_factory=uuid4)
    request: ResidualCleanupRequest
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    items: tuple[FreshResidualCandidate, ...]
    selected_count: int = Field(ge=1)
    eligible_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    manual_review_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_totals(self) -> Self:
        """Make mixed-batch status and item totals deterministic."""
        if self.selected_count != len(self.items):
            raise ValueError("Cleanup assessment selected count does not match items")
        expected = {
            CleanupEligibilityDecision.ELIGIBLE: self.eligible_count,
            CleanupEligibilityDecision.BLOCKED: self.blocked_count,
            CleanupEligibilityDecision.MANUAL_REVIEW: self.manual_review_count,
        }
        for decision, count in expected.items():
            if sum(item.eligibility is decision for item in self.items) != count:
                raise ValueError(f"Cleanup assessment {decision.value} count does not match")
        return self

    @property
    def all_eligible(self) -> bool:
        """Return whether the original exact selection can proceed as one batch."""
        return self.eligible_count == self.selected_count

    def canonical_digest(self) -> str:
        """Hash every fresh row, including reasons for blocked selections."""
        return canonical_digest(self.model_dump(mode="json"))

    def invariant_digest(self) -> str:
        """Hash the selected request and fresh evidence without transient IDs or time."""
        payload = self.model_dump(
            mode="json",
            exclude={
                "assessment_id": True,
                "generated_at": True,
                "items": {"__all__": {"generated_at"}},
            },
        )
        return canonical_digest(payload)


class PlannedResidualCleanupItem(FrozenModel):
    """One eligible item whose action is fixed to Windows Recycle Bin placement."""

    item_ref: UUID = Field(default_factory=uuid4)
    operation_id: UUID = Field(default_factory=uuid4)
    sequence: int = Field(ge=0)
    candidate: FreshResidualCandidate
    action: ResidualCleanupAction = ResidualCleanupAction.MOVE_TO_RECYCLE_BIN
    tool_name: str = "software.residuals.trash"
    rollback_level: RollbackLevel = RollbackLevel.MANUAL

    @model_validator(mode="after")
    def require_eligible_candidate(self) -> Self:
        """Prevent blocked or manually reviewed candidates from entering a plan."""
        if self.candidate.eligibility is not CleanupEligibilityDecision.ELIGIBLE:
            raise ValueError("Only freshly eligible residuals may enter a cleanup plan")
        if self.tool_name != "software.residuals.trash":
            raise ValueError("Residual cleanup must use its dedicated trash tool")
        if self.rollback_level is not RollbackLevel.MANUAL:
            raise ValueError("Residual cleanup recovery is MANUAL")
        return self

    def canonical_digest(self) -> str:
        """Hash the exact candidate, action, tool, and recovery contract."""
        return canonical_digest(self.model_dump(mode="json"))


class ResidualCleanupPlan(FrozenModel):
    """Complete executable batch compiled only from an all-eligible assessment."""

    plan_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID = Field(default_factory=uuid4)
    source_report_id: UUID
    request_id: UUID
    assessment_id: UUID
    assessment_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: tuple[PlannedResidualCleanupItem, ...] = Field(min_length=1, max_length=100)
    total_items: int = Field(ge=1)
    contained_object_count: int = Field(ge=1)
    total_bytes: int = Field(ge=0)
    largest_item_bytes: int = Field(ge=0)
    risk_level: RiskLevel
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    requires_plan_confirmation: bool = True
    requires_runtime_confirmation: bool = True
    rollback_level: RollbackLevel = RollbackLevel.MANUAL

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        """Enforce ordered unique items, exact totals, R2 risk, and two confirmations."""
        if self.expires_at <= self.created_at:
            raise ValueError("Residual cleanup plan expiry must follow creation")
        if self.risk_level not in {RiskLevel.R2, RiskLevel.R2_HIGH_IMPACT}:
            raise ValueError("Residual cleanup plans must be R2")
        if not self.requires_plan_confirmation or not self.requires_runtime_confirmation:
            raise ValueError("Residual cleanup requires both confirmation tiers")
        if self.rollback_level is not RollbackLevel.MANUAL:
            raise ValueError("Residual cleanup recovery is MANUAL")
        if self.total_items != len(self.items):
            raise ValueError("Residual cleanup item total does not match")
        if [item.sequence for item in self.items] != list(range(len(self.items))):
            raise ValueError("Residual cleanup item sequence must be contiguous")
        if len({item.item_ref for item in self.items}) != len(self.items):
            raise ValueError("Residual cleanup item references must be unique")
        paths = [str(item.candidate.path).casefold() for item in self.items]
        if len(paths) != len(set(paths)):
            raise ValueError("Residual cleanup paths must be unique")
        if self.contained_object_count != sum(
            item.candidate.material.tree.object_count
            for item in self.items
            if item.candidate.material is not None
        ):
            raise ValueError("Residual cleanup contained-object total does not match")
        if self.total_bytes != sum(
            item.candidate.material.tree.total_size_bytes
            for item in self.items
            if item.candidate.material is not None
        ):
            raise ValueError("Residual cleanup byte total does not match")
        if self.largest_item_bytes != max(
            item.candidate.material.tree.largest_item_bytes
            for item in self.items
            if item.candidate.material is not None
        ):
            raise ValueError("Residual cleanup largest item does not match")
        return self

    def canonical_digest(self) -> str:
        """Hash the exact plan used by persistence and both confirmations."""
        return canonical_digest(self.model_dump(mode="json"))


class ResidualCleanupPreview(FrozenModel):
    """Fresh user-visible Preview bound to exact eligible objects and risk."""

    preview_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID
    plan_id: UUID
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    items: tuple[PlannedResidualCleanupItem, ...]
    item_set_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    total_items: int = Field(ge=1)
    contained_object_count: int = Field(ge=1)
    total_bytes: int = Field(ge=0)
    largest_item_bytes: int = Field(ge=0)
    risk_level: RiskLevel
    recovery_level: RollbackLevel = RollbackLevel.MANUAL
    executable: bool = True

    @model_validator(mode="after")
    def validate_preview(self) -> Self:
        """Ensure a displayed Preview contains the complete immutable batch."""
        if self.expires_at <= self.generated_at:
            raise ValueError("Residual cleanup Preview expiry must follow generation")
        if self.total_items != len(self.items):
            raise ValueError("Residual cleanup Preview item count does not match")
        if self.recovery_level is not RollbackLevel.MANUAL:
            raise ValueError("Residual cleanup Preview must show MANUAL recovery")
        if not self.executable:
            raise ValueError("Blocked selections remain assessments, not executable Previews")
        return self

    def canonical_digest(self) -> str:
        """Hash the exact content displayed in both confirmation dialogs."""
        return canonical_digest(self.model_dump(mode="json"))


class ResidualCleanupTrashRequest(FrozenModel):
    """Reference-only write request resolved from durable state inside the tool."""

    transaction_id: UUID
    cleanup_plan_id: UUID
    preview_id: UUID
    validated_item_ref: UUID


class ResidualCleanupTrashResult(FrozenModel):
    """One low-level Recycle Bin result before the service performs final verification."""

    item_ref: UUID
    original_identity: FileState
    outcome: RecycleBinResult


class ResidualCleanupItemResult(FrozenModel):
    """Terminal per-item result with identity-aware verification and recovery evidence."""

    item_ref: UUID
    operation_id: UUID
    sequence: int = Field(ge=0)
    source_candidate_id: UUID
    path: Path
    state: ResidualCleanupItemState
    verification_status: ResidualVerificationStatus
    recycle_result: RecycleBinResult | None = None
    recovery_id: UUID | None = None
    message: str = Field(min_length=1, max_length=1_000)


class ResidualCleanupExecutionReport(FrozenModel):
    """Truthful batch result that never implies skipped items were handled."""

    transaction_id: UUID
    plan_id: UUID
    final_state: ResidualCleanupTransactionState
    completed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    total_size_bytes: int = Field(ge=0)
    recovery_level: RollbackLevel = RollbackLevel.MANUAL
    results: tuple[ResidualCleanupItemResult, ...]
