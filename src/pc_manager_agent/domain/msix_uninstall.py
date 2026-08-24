"""Provider-neutral contracts for controlled current-user MSIX removal."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.software_uninstall_analysis import (
    SoftwareSafetyClass,
    canonical_digest,
)


class MsixPackageType(StrEnum):
    """Deterministic package categories; V1 writes only ordinary user apps."""

    USER_MSIX_APP = "user_msix_app"
    FRAMEWORK = "framework"
    RESOURCE = "resource"
    BUNDLE = "bundle"
    OPTIONAL = "optional"
    SYSTEM = "system"
    PROVISIONED = "provisioned"
    DEPENDENCY = "dependency"
    SECURITY = "security"
    UNKNOWN = "unknown"


class MsixScope(StrEnum):
    """Package registration scope understood by the Stage 4D2C2 boundary."""

    CURRENT_USER = "current_user"
    OTHER_USER = "other_user"
    ALL_USERS = "all_users"
    PROVISIONED = "provisioned"
    UNKNOWN = "unknown"


class MsixInventoryState(StrEnum):
    """Completeness of a bounded current-user inventory."""

    COMPLETE = "complete"
    FAILED = "failed"
    TRUNCATED = "truncated"


class MsixDependencyState(StrEnum):
    """Confidence in the dependency relationship snapshot."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class MsixRemovalDecision(StrEnum):
    """Final deterministic removal-policy decision."""

    ALLOW = "allow"
    BLOCK = "block"
    READ_ONLY = "read_only"
    UNSUPPORTED = "unsupported"


class MsixPreflightState(StrEnum):
    """Read-only process/service preflight result."""

    READY = "ready"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class MsixRemovalResultCategory(StrEnum):
    """Observed Windows deployment result; it is not the final verdict."""

    REMOVAL_COMPLETED = "removal_completed"
    PACKAGE_NOT_FOUND = "package_not_found"
    ACCESS_DENIED = "access_denied"
    PACKAGES_IN_USE = "packages_in_use"
    DEPLOYMENT_ERROR = "deployment_error"
    DEPENDENCY_ERROR = "dependency_error"
    CANCELLED_BEFORE_DISPATCH = "cancelled_before_dispatch"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class MsixVerificationState(StrEnum):
    """Final verdict from fresh package and software inventories."""

    VERIFIED_REMOVED = "verified_removed"
    PACKAGE_STILL_REGISTERED = "package_still_registered"
    PACKAGE_INSTANCE_REPLACED = "package_instance_replaced"
    SOFTWARE_PRESENT = "software_present"
    COMPLETED_UNVERIFIED = "completed_unverified"
    ALREADY_REMOVED = "already_removed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class MsixTransactionState(StrEnum):
    """Durable single-dispatch lifecycle; interrupted work is never retried."""

    PREVIEWED = "previewed"
    AWAITING_PLAN_CONFIRMATION = "awaiting_plan_confirmation"
    PLAN_CONFIRMED = "plan_confirmed"
    VALIDATING = "validating"
    AWAITING_RUNTIME_CONFIRMATION = "awaiting_runtime_confirmation"
    DISPATCHING = "dispatching"
    EXECUTING = "executing"
    REMOVAL_IN_PROGRESS = "removal_in_progress"
    VERIFYING = "verifying"
    VERIFIED_REMOVED = "verified_removed"
    COMPLETED_UNVERIFIED = "completed_unverified"
    ACCESS_DENIED = "access_denied"
    CANCELLED = "cancelled"
    FAILED = "failed"
    BLOCKED = "blocked"
    INTERRUPTED = "interrupted"


class MsixFamilyIdentity(FrozenModel):
    """Stable package family identity across Store version updates."""

    family_name: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    publisher_id: str = Field(min_length=1, max_length=255)

    def canonical_digest(self) -> str:
        """Hash the stable family identity."""
        return canonical_digest(self.model_dump(mode="json"))


class MsixInstanceIdentity(FrozenModel):
    """Exact installed package instance that one confirmation authorizes."""

    full_name: str = Field(min_length=1, max_length=500)
    version: str = Field(min_length=1, max_length=100)
    architecture: str = Field(min_length=1, max_length=50)
    resource_id: str = Field(default="", max_length=255)

    def canonical_digest(self) -> str:
        """Hash version-sensitive package identity."""
        return canonical_digest(self.model_dump(mode="json"))


class RawMsixPackageRecord(FrozenModel):
    """Structured WinRT data retained before deterministic normalization."""

    family_name: str = Field(min_length=1, max_length=255)
    full_name: str = Field(min_length=1, max_length=500)
    name: str = Field(min_length=1, max_length=255)
    publisher_id: str = Field(min_length=1, max_length=255)
    publisher_display_name: str | None = Field(default=None, max_length=500)
    version: str = Field(min_length=1, max_length=100)
    architecture: str = Field(min_length=1, max_length=50)
    resource_id: str = Field(default="", max_length=255)
    display_name: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=2_000)
    is_framework: bool
    is_resource: bool
    is_bundle: bool
    is_optional: bool
    is_development_mode: bool
    is_stub: bool
    signature_kind: str = Field(min_length=1, max_length=100)
    status_ok: bool
    has_app_entry: bool
    installed_path: str | None = Field(default=None, max_length=2_000)


class MsixPackageIdentity(FrozenModel):
    """Exact current-user family and instance identity with type evidence."""

    family: MsixFamilyIdentity
    instance: MsixInstanceIdentity
    scope: MsixScope
    package_type: MsixPackageType
    current_user_registered: bool
    provisioned_state: str = "not_queried"
    is_framework: bool
    is_resource: bool
    is_bundle: bool
    is_optional: bool
    is_development_mode: bool
    is_stub: bool
    signature_kind: str
    status_ok: bool

    @model_validator(mode="after")
    def enforce_current_user_claim(self) -> Self:
        """Reject a contradictory current-user execution identity."""
        if self.scope is MsixScope.CURRENT_USER and not self.current_user_registered:
            raise ValueError("current-user scope requires current-user registration")
        if self.provisioned_state != "not_queried":
            raise ValueError("Stage 4D2C2 must not query provisioned packages")
        return self

    def canonical_digest(self) -> str:
        """Hash every package fact used for authorization."""
        return canonical_digest(self.model_dump(mode="json"))


class NormalizedMsixPackage(FrozenModel):
    """Safe package projection used by orchestration and UI."""

    identity: MsixPackageIdentity
    display_name: str = Field(min_length=1, max_length=500)
    publisher_display_name: str | None = Field(default=None, max_length=500)
    installed_path: str | None = Field(default=None, max_length=2_000)


class MsixPackageInventory(FrozenModel):
    """Bounded current-user-only package inventory."""

    state: MsixInventoryState
    packages: tuple[NormalizedMsixPackage, ...]
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    warnings: tuple[str, ...] = ()


class MsixTargetQuery(FrozenModel):
    """Exact digest or display-only discovery query; writes never use display text."""

    identity_digest: str | None = Field(default=None, min_length=64, max_length=64)
    family_name: str | None = Field(default=None, min_length=1, max_length=255)
    full_name: str | None = Field(default=None, min_length=1, max_length=500)
    display_query: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_selector(self) -> Self:
        """Require an exact selector or a bounded discovery term."""
        if not any((self.identity_digest, self.family_name, self.full_name, self.display_query)):
            raise ValueError("MSIX target query requires a selector")
        return self


class ResolvedMsixPackage(FrozenModel):
    """One exact package or an explicit ambiguous/absent result."""

    query: MsixTargetQuery
    selected: NormalizedMsixPackage | None = None
    candidates: tuple[NormalizedMsixPackage, ...] = Field(default=(), max_length=100)
    ambiguous: bool
    reason: str = Field(min_length=1, max_length=1_000)


class MsixDependencyReference(FrozenModel):
    """Identity-only dependency evidence without manifest or user-data content."""

    family_name: str = Field(min_length=1, max_length=255)
    full_name: str = Field(min_length=1, max_length=500)
    package_type: MsixPackageType


class MsixDependencySnapshot(FrozenModel):
    """Direct and reverse relationship evidence used by the fail-closed policy."""

    state: MsixDependencyState
    target_identity_digest: str = Field(min_length=64, max_length=64)
    direct_dependencies: tuple[MsixDependencyReference, ...] = ()
    reverse_dependents: tuple[MsixDependencyReference, ...] = ()
    orphan_dependency_risk: bool
    warnings: tuple[str, ...] = ()

    def canonical_digest(self) -> str:
        """Hash all relationship facts and completeness flags."""
        return canonical_digest(self.model_dump(mode="json"))


class MsixPreflight(FrozenModel):
    """Read-only lifecycle evidence; it grants no process/service authority."""

    state: MsixPreflightState
    process_probe_complete: bool
    service_probe_complete: bool
    related_process_count: int = Field(ge=0)
    related_service_count: int = Field(ge=0)
    another_uninstall_active: bool
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def canonical_digest(self) -> str:
        """Hash all bounded preflight evidence."""
        return canonical_digest(self.model_dump(mode="json"))


class MsixRemovalAssessment(FrozenModel):
    """Package type, scope, software safety, and dependency policy result."""

    decision: MsixRemovalDecision
    safety_class: SoftwareSafetyClass
    risk_level: RiskLevel
    reasons: tuple[str, ...]

    @model_validator(mode="after")
    def allow_only_ordinary_r2(self) -> Self:
        """Keep every executable decision inside the narrow V1 boundary."""
        if self.decision is MsixRemovalDecision.ALLOW:
            if self.safety_class is not SoftwareSafetyClass.USER_APPLICATION:
                raise ValueError("only ordinary user applications may be allowed")
            if self.risk_level is not RiskLevel.R2:
                raise ValueError("allowed MSIX removal must be R2")
        return self

    def canonical_digest(self) -> str:
        """Hash the final deterministic safety decision."""
        return canonical_digest(self.model_dump(mode="json"))


class MsixUninstallPlan(FrozenModel):
    """Immutable one-package plan for the sole Stage 4D2C2 write tool."""

    plan_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID = Field(default_factory=uuid4)
    operation_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_goal: str = Field(min_length=1, max_length=2_000)
    package_identity_digest: str = Field(min_length=64, max_length=64)
    dependency_digest: str = Field(min_length=64, max_length=64)
    assessment_digest: str = Field(min_length=64, max_length=64)
    preflight_digest: str = Field(min_length=64, max_length=64)
    tool_name: str = "software.uninstall.msix"
    risk_level: RiskLevel = RiskLevel.R2
    rollback_level: RollbackLevel = RollbackLevel.NONE
    requires_plan_confirmation: bool = True
    requires_runtime_confirmation: bool = True

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        """Require one irreversible, twice-confirmed R2 operation."""
        if self.tool_name != "software.uninstall.msix":
            raise ValueError("Stage 4D2C2 has exactly one write tool")
        if self.risk_level is not RiskLevel.R2 or self.rollback_level is not RollbackLevel.NONE:
            raise ValueError("MSIX removal is R2 with no automatic rollback")
        if not self.requires_plan_confirmation or not self.requires_runtime_confirmation:
            raise ValueError("MSIX removal requires both confirmations")
        return self

    def canonical_digest(self) -> str:
        """Hash every plan field that affects authorization."""
        return canonical_digest(self.model_dump(mode="json"))


class MsixDataImpact(FrozenModel):
    """Fixed Windows removal semantics shown in the immediate confirmation."""

    roamable_data_preserved: bool = True
    local_state_may_be_removed: bool = True
    orphan_dependencies_may_be_removed_by_windows: bool = True
    agent_extra_data_deletion: bool = False


class MsixUninstallPreview(FrozenModel):
    """Expiring Preview bound to identity, dependency, policy, and preflight evidence."""

    preview_id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    transaction_id: UUID
    operation_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    package: NormalizedMsixPackage
    dependencies: MsixDependencySnapshot
    assessment: MsixRemovalAssessment
    preflight: MsixPreflight
    data_impact: MsixDataImpact = Field(default_factory=MsixDataImpact)
    executable: bool
    rollback_level: RollbackLevel = RollbackLevel.NONE

    @model_validator(mode="after")
    def bind_evidence(self) -> Self:
        """Reject stale, contradictory, blocked, or falsely reversible Preview data."""
        if self.expires_at <= self.generated_at:
            raise ValueError("MSIX Preview must expire")
        expected = self.package.identity.canonical_digest()
        if self.dependencies.target_identity_digest != expected:
            raise ValueError("dependency snapshot targets another package")
        permitted = (
            self.assessment.decision is MsixRemovalDecision.ALLOW
            and self.preflight.state is MsixPreflightState.READY
            and self.dependencies.state is MsixDependencyState.COMPLETE
            and not self.dependencies.reverse_dependents
            and not self.dependencies.orphan_dependency_risk
            and self.package.identity.package_type is MsixPackageType.USER_MSIX_APP
            and self.package.identity.scope is MsixScope.CURRENT_USER
        )
        if self.executable != permitted:
            raise ValueError("MSIX Preview executable flag contradicts safety evidence")
        if self.rollback_level is not RollbackLevel.NONE:
            raise ValueError("MSIX removal cannot claim automatic rollback")
        return self

    def invariant_digest(self) -> str:
        """Hash all facts that must remain unchanged between confirmations."""
        return canonical_digest(
            {
                "package": self.package.identity.canonical_digest(),
                "dependencies": self.dependencies.canonical_digest(),
                "assessment": self.assessment.canonical_digest(),
                "preflight": self.preflight.canonical_digest(),
                "data_impact": self.data_impact.model_dump(mode="json"),
                "executable": self.executable,
            }
        )

    def canonical_digest(self) -> str:
        """Hash the complete expiring Preview."""
        return canonical_digest(self.model_dump(mode="json"))


class ValidatedMsixRemovalAction(FrozenModel):
    """Internal capability for one exact current-user removal with fixed options."""

    transaction_id: UUID
    identity: MsixPackageIdentity
    dependency_digest: str = Field(min_length=64, max_length=64)
    assessment_digest: str = Field(min_length=64, max_length=64)
    preflight_digest: str = Field(min_length=64, max_length=64)
    preserve_roamable_application_data: bool = True
    validated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def enforce_narrow_action(self) -> Self:
        """Prevent caller-selected options, scope expansion, and special package types."""
        if self.identity.scope is not MsixScope.CURRENT_USER:
            raise ValueError("MSIX removal is current-user only")
        if self.identity.package_type is not MsixPackageType.USER_MSIX_APP:
            raise ValueError("only ordinary user MSIX apps may be removed")
        if not self.preserve_roamable_application_data:
            raise ValueError("roamable application data preservation is mandatory")
        return self


class MsixUninstallRequest(FrozenModel):
    """Reference-bound input accepted by the registered write tool."""

    transaction_id: UUID
    operation_id: UUID
    plan_id: UUID
    preview_id: UUID
    action: ValidatedMsixRemovalAction

    @model_validator(mode="after")
    def bind_transaction(self) -> Self:
        """Bind the internal capability to the durable transaction."""
        if self.action.transaction_id != self.transaction_id:
            raise ValueError("MSIX action belongs to another transaction")
        return self


class MsixDeploymentResult(FrozenModel):
    """Narrow adapter observation with no command or arbitrary option surface."""

    category: MsixRemovalResultCategory
    activity_id: str | None = Field(default=None, max_length=100)
    error_code: int | None = None
    error_text: str | None = Field(default=None, max_length=1_000)
    dispatched: bool


class MsixVerification(FrozenModel):
    """Fresh-inventory final verdict."""

    state: MsixVerificationState
    original_full_name_present: bool
    same_family_instances: tuple[str, ...] = ()
    software_identity_present: bool | None = None
    reason: str = Field(min_length=1, max_length=1_000)


class MsixResidualReport(FrozenModel):
    """Exact known-path metadata only; no enumeration or deletion authority."""

    known_install_path_present: bool | None
    user_data_not_enumerated: bool = True
    user_data_deleted_by_agent: bool = False
    note: str = (
        "Agent did not enumerate or delete package data. Windows package removal may remove "
        "LocalState; roaming data was requested to be preserved."
    )


class MsixUninstallResult(FrozenModel):
    """Registered-tool output combining adapter, verification, and residual evidence."""

    transaction_id: UUID
    deployment: MsixDeploymentResult
    verification: MsixVerification
    residual: MsixResidualReport
    rollback_level: RollbackLevel = RollbackLevel.NONE
