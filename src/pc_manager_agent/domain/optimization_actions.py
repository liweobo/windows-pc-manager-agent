"""Reference-only review, workflow and outcome models for Stage 4E3.

None of these objects implements or contains a business execution authorization.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.system_optimization import OptimizationConfidence, RecommendationType


class RecommendationActionability(StrEnum):
    """Whether a suggestion can enter a review flow, never whether it may execute."""

    INFORMATIONAL = "INFORMATIONAL"
    REVIEW_ONLY = "REVIEW_ONLY"
    ROUTABLE = "ROUTABLE"
    BLOCKED = "BLOCKED"
    NOT_CURRENTLY_SUPPORTED = "NOT_CURRENTLY_SUPPORTED"


class OptimizationTargetDomain(StrEnum):
    """Complete set of business destinations; no generic privileged destination exists."""

    PROCESS = "PROCESS"
    STARTUP = "STARTUP"
    SERVICE = "SERVICE"
    SOFTWARE = "SOFTWARE"
    SOFTWARE_RESIDUAL = "SOFTWARE_RESIDUAL"
    SYSTEM_CLEANUP = "SYSTEM_CLEANUP"
    PERSONAL_STORAGE = "PERSONAL_STORAGE"
    INFORMATIONAL = "INFORMATIONAL"
    UNSUPPORTED = "UNSUPPORTED"


class OptimizationCapability(StrEnum):
    """Allowlisted preparation capabilities, intentionally not writer tool names."""

    PROCESS_REVIEW = "process.review"
    STARTUP_REVIEW = "startup.review"
    SERVICE_REVIEW = "service.review"
    SOFTWARE_REVIEW = "software.review"
    RESIDUAL_REVIEW = "residual.review"
    CLEANUP_REVIEW = "cleanup.review"
    RECYCLE_BIN_REVIEW = "recycle_bin.review"
    PERSONAL_STORAGE_REVIEW = "personal_storage.review"
    STORAGE_OVERVIEW = "storage.overview"
    NONE = "none"

    @property
    def domain(self) -> OptimizationTargetDomain:
        """Return the immutable semantic owner of this preparation capability."""
        return {
            self.PROCESS_REVIEW: OptimizationTargetDomain.PROCESS,
            self.STARTUP_REVIEW: OptimizationTargetDomain.STARTUP,
            self.SERVICE_REVIEW: OptimizationTargetDomain.SERVICE,
            self.SOFTWARE_REVIEW: OptimizationTargetDomain.SOFTWARE,
            self.RESIDUAL_REVIEW: OptimizationTargetDomain.SOFTWARE_RESIDUAL,
            self.CLEANUP_REVIEW: OptimizationTargetDomain.SYSTEM_CLEANUP,
            self.RECYCLE_BIN_REVIEW: OptimizationTargetDomain.SYSTEM_CLEANUP,
            self.PERSONAL_STORAGE_REVIEW: OptimizationTargetDomain.PERSONAL_STORAGE,
            self.STORAGE_OVERVIEW: OptimizationTargetDomain.INFORMATIONAL,
            self.NONE: OptimizationTargetDomain.UNSUPPORTED,
        }[self]


class PreparationStatus(StrEnum):
    """Truthful result of preparing or navigating to an independent business flow."""

    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    NEEDS_TARGET_SELECTION = "NEEDS_TARGET_SELECTION"
    NO_LONGER_APPLICABLE = "NO_LONGER_APPLICABLE"
    BLOCKED = "BLOCKED"
    STALE = "STALE"
    UNSUPPORTED = "UNSUPPORTED"
    FAILED = "FAILED"


class OptimizationUISurface(StrEnum):
    """Fixed UI destinations; arbitrary pages, commands and URLs are not accepted."""

    PROCESS = "PROCESS"
    STARTUP = "STARTUP"
    SERVICE_READONLY = "SERVICE_READONLY"
    SOFTWARE = "SOFTWARE"
    RESIDUAL = "RESIDUAL"
    CLEANUP = "CLEANUP"
    RECYCLE_BIN = "RECYCLE_BIN"
    PERSONAL_STORAGE = "PERSONAL_STORAGE"
    OVERVIEW = "OVERVIEW"


class OptimizationRecommendationReference(FrozenModel):
    """Resolve a recommendation exclusively from the current local report store."""

    source_report_id: UUID
    recommendation_id: UUID


class OptimizationActionRoute(FrozenModel):
    """Digest-bound navigation intent that cannot be passed to a domain writer."""

    route_id: UUID = Field(default_factory=uuid4)
    source_report_id: UUID
    source_snapshot_id: UUID
    recommendation_id: UUID
    recommendation_type: RecommendationType
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_domain: OptimizationTargetDomain
    target_capability: OptimizationCapability
    actionability: RecommendationActionability
    evidence_references: tuple[UUID, ...] = Field(max_length=100)
    requires_fresh_resolution: Literal[True] = True
    requires_fresh_preview: Literal[True] = True
    requires_confirmation: Literal[True] = True
    causes_system_change: Literal[False] = False
    route_reason_codes: tuple[str, ...] = Field(min_length=1, max_length=20)
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def check_destination(self) -> OptimizationActionRoute:
        """Reject a capability being relabelled as a different business domain."""
        if self.target_capability is not OptimizationCapability.NONE:
            if self.target_domain is not self.target_capability.domain:
                raise ValueError("Capability and domain disagree")
        elif self.actionability is RecommendationActionability.ROUTABLE:
            raise ValueError("No-capability routes cannot be routable")
        return self


class OptimizationActionPreparationResult(FrozenModel):
    """Reference-only handoff response; confirmation material stays inside its domain."""

    handoff_id: UUID = Field(default_factory=uuid4)
    route_id: UUID
    recommendation_id: UUID
    target_domain: OptimizationTargetDomain
    status: PreparationStatus
    fresh_context_id: UUID | None = None
    next_ui_surface: OptimizationUISurface | None = None
    domain_plan_id: UUID | None = None
    requires_user_confirmation: Literal[True] = True
    blocked_reason_codes: tuple[str, ...] = Field(default=(), max_length=20)
    causes_system_change: Literal[False] = False


class OptimizationSessionStatus(StrEnum):
    """Review-container status, never an authorization state."""

    CREATED = "CREATED"
    IN_PROGRESS = "IN_PROGRESS"
    PARTIALLY_APPLIED = "PARTIALLY_APPLIED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


class RecommendationActionState(StrEnum):
    """Per-recommendation progress; executed requires a corroborated domain receipt."""

    PENDING = "PENDING"
    REVIEWED = "REVIEWED"
    ROUTED = "ROUTED"
    NO_LONGER_APPLICABLE = "NO_LONGER_APPLICABLE"
    BLOCKED = "BLOCKED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    STALE = "STALE"


class OptimizationOutcomeType(StrEnum):
    """Preserve uncertainty instead of converting every terminal event into success."""

    APPLIED_VERIFIED = "APPLIED_VERIFIED"
    APPLIED_UNVERIFIED = "APPLIED_UNVERIFIED"
    BLOCKED = "BLOCKED"
    NO_LONGER_APPLICABLE = "NO_LONGER_APPLICABLE"
    USER_CANCELLED = "USER_CANCELLED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class BenefitObservationStatus(StrEnum):
    """Separate verified action state from measured and attributable benefit."""

    ACTION_STATE_VERIFIED = "ACTION_STATE_VERIFIED"
    BENEFIT_NOT_MEASURED = "BENEFIT_NOT_MEASURED"
    BENEFIT_PARTIALLY_OBSERVED = "BENEFIT_PARTIALLY_OBSERVED"
    BENEFIT_OBSERVED = "BENEFIT_OBSERVED"


class OptimizationMetric(StrEnum):
    """Finite observations, deliberately excluding speed or boot-improvement percentages."""

    AVAILABLE_MEMORY_BYTES = "AVAILABLE_MEMORY_BYTES"
    DISK_FREE_BYTES = "DISK_FREE_BYTES"
    STARTUP_ENABLED_COUNT = "STARTUP_ENABLED_COUNT"
    ORIGINAL_OBJECT_COUNT = "ORIGINAL_OBJECT_COUNT"
    SOFTWARE_ENTRY_COUNT = "SOFTWARE_ENTRY_COUNT"


class OptimizationBenefitObservation(FrozenModel):
    """A before/after observation is not evidence of causation."""

    metric: OptimizationMetric
    before: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    after: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    measured: bool = False
    attribution_confidence: OptimizationConfidence = OptimizationConfidence.UNKNOWN
    caused_by_action: Literal[False] = False

    @model_validator(mode="after")
    def check_measurement(self) -> OptimizationBenefitObservation:
        """Only complete observations may be called measured; attribution stays conservative."""
        if self.measured != (self.before is not None and self.after is not None):
            raise ValueError("Measured requires both before and after")
        if self.attribution_confidence not in {
            OptimizationConfidence.LOW,
            OptimizationConfidence.UNKNOWN,
        }:
            raise ValueError("Short targeted observations cannot prove attribution")
        return self


class OptimizationActionOutcome(FrozenModel):
    """Privacy-minimized, domain-correlated outcome; not a mutable domain result."""

    recommendation_id: UUID
    target_domain: OptimizationTargetDomain
    handoff_id: UUID
    domain_plan_id: UUID | None = None
    domain_transaction_id: UUID | None = None
    domain_confirmation_id: UUID | None = None
    domain_risk: RiskLevel | None = None
    recovery_level: RollbackLevel = RollbackLevel.NONE
    outcome: OptimizationOutcomeType
    verified: bool = False
    user_cancelled: bool = False
    summary_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,99}$")
    completed_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    authorization_source: Literal["domain_confirmation"] | None = None
    benefit_status: BenefitObservationStatus = BenefitObservationStatus.BENEFIT_NOT_MEASURED
    benefit_observations: tuple[OptimizationBenefitObservation, ...] = Field(
        default=(), max_length=10
    )

    @model_validator(mode="after")
    def check_evidence(self) -> OptimizationActionOutcome:
        """Applied outcomes require domain lineage; verified cannot describe an uncertain result."""
        applied = self.outcome in {
            OptimizationOutcomeType.APPLIED_VERIFIED,
            OptimizationOutcomeType.APPLIED_UNVERIFIED,
        }
        if self.verified != (self.outcome is OptimizationOutcomeType.APPLIED_VERIFIED):
            raise ValueError("Outcome and verification disagree")
        if applied and (
            self.domain_plan_id is None
            or self.domain_transaction_id is None
            or self.domain_confirmation_id is None
            or self.domain_risk is None
            or self.authorization_source != "domain_confirmation"
        ):
            raise ValueError("Applied outcomes need complete domain authorization lineage")
        if self.benefit_status is BenefitObservationStatus.BENEFIT_OBSERVED:
            raise ValueError("V1 has no attributable long-term benefit measurement")
        return self


class OptimizationSessionItem(FrozenModel):
    """One recommendation's progress and optional verified domain lineage."""

    recommendation_id: UUID
    state: RecommendationActionState = RecommendationActionState.PENDING
    handoff_id: UUID | None = None
    outcome: OptimizationActionOutcome | None = None


class OptimizationSession(FrozenModel):
    """A bounded review container with no global confirmation, execution or undo."""

    session_id: UUID = Field(default_factory=uuid4)
    source_report_id: UUID
    source_snapshot_id: UUID
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(default=1, ge=1)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    status: OptimizationSessionStatus = OptimizationSessionStatus.CREATED
    items: tuple[OptimizationSessionItem, ...] = Field(min_length=1, max_length=50)
    global_authorization: Literal[False] = False
    global_undo_available: Literal[False] = False

    @model_validator(mode="after")
    def check_items(self) -> OptimizationSession:
        """Reject duplicate recommendations, mismatched outcomes and concurrent handoffs."""
        ids = tuple(item.recommendation_id for item in self.items)
        if len(ids) != len(set(ids)):
            raise ValueError("Session recommendations must be unique")
        if sum(item.state is RecommendationActionState.ROUTED for item in self.items) > 1:
            raise ValueError("Only one domain handoff may be active in a session")
        for item in self.items:
            if (
                item.outcome is not None
                and item.outcome.recommendation_id != item.recommendation_id
            ):
                raise ValueError("Outcome belongs to another recommendation")
        return self


class OptimizationSessionCreateRequest(FrozenModel):
    """Explicitly selected recommendation IDs; selection is not bulk execution consent."""

    source_report_id: UUID
    recommendation_ids: tuple[UUID, ...] = Field(min_length=1, max_length=50)


class OptimizationSessionReference(FrozenModel):
    """Read one locally persisted session summary without accepting a session body."""

    session_id: UUID
