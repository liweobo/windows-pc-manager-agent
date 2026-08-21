"""Provider-neutral contracts for controlled Stage 4D2A MSI uninstall execution."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.software_uninstall_analysis import (
    NormalizedInstalledSoftware,
    SoftwareSafetyClass,
    SoftwareTargetQuery,
    UninstallCapability,
    canonical_digest,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareArchitecture, SoftwareScope


class MsiInstallContext(StrEnum):
    """Windows Installer registration context for one exact product instance."""

    USER_MANAGED = "user_managed"
    USER_UNMANAGED = "user_unmanaged"
    MACHINE = "machine"
    UNKNOWN = "unknown"


class MsiExecutionDecision(StrEnum):
    """Final deterministic permission decision before confirmation."""

    ALLOW = "allow"
    BLOCK = "block"


class MsiPreflightState(StrEnum):
    """Whether execution preflight has complete, non-blocking evidence."""

    READY = "ready"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class MsiInstallerResultCategory(StrEnum):
    """Normalized result of the fixed Windows Installer client."""

    SUCCESS = "success"
    SUCCESS_REBOOT_REQUIRED = "success_reboot_required"
    USER_CANCELLED = "user_cancelled"
    ANOTHER_INSTALL_IN_PROGRESS = "another_install_in_progress"
    PRODUCT_NOT_INSTALLED = "product_not_installed"
    PRIVILEGE_REQUIRED = "privilege_required"
    POLICY_BLOCKED = "policy_blocked"
    INSTALLER_FAILURE = "installer_failure"
    REBOOT_INITIATED_UNEXPECTED = "reboot_initiated_unexpected"
    MONITORING_DETACHED = "monitoring_detached"
    LAUNCH_FAILED = "launch_failed"
    UNKNOWN = "unknown"


class MsiVerificationState(StrEnum):
    """Final observed state after installer completion and fresh inventory."""

    VERIFIED_REMOVED = "verified_removed"
    COMPLETED_UNVERIFIED = "completed_unverified"
    REMOVED_WITH_UNEXPECTED_INSTALLER_RESULT = "removed_with_unexpected_installer_result"
    TARGET_REPLACED_OR_UPGRADED = "target_replaced_or_upgraded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class MsiUninstallTransactionState(StrEnum):
    """Durable lifecycle; no state implies rollback or automatic retry."""

    PREVIEWED = "previewed"
    AWAITING_PLAN_CONFIRMATION = "awaiting_plan_confirmation"
    PLAN_CONFIRMED = "plan_confirmed"
    VALIDATING = "validating"
    AWAITING_RUNTIME_CONFIRMATION = "awaiting_runtime_confirmation"
    CONFIRMED = "confirmed"
    PREFLIGHT = "preflight"
    DISPATCHING = "dispatching"
    EXECUTING = "executing"
    WAITING = "waiting"
    INSTALLER_COMPLETED = "installer_completed"
    VERIFYING = "verifying"
    VERIFIED_REMOVED = "verified_removed"
    REBOOT_REQUIRED = "reboot_required"
    USER_CANCELLED = "user_cancelled"
    PRIVILEGE_REQUIRED = "privilege_required"
    COMPLETED_UNVERIFIED = "completed_unverified"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class MsiProductRegistration(FrozenModel):
    """One product instance returned by the Windows Installer inventory API."""

    product_code: str = Field(pattern=r"^\{[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}\}$")
    context: MsiInstallContext
    user_sid_digest: str | None = Field(default=None, min_length=64, max_length=64)
    product_name: str | None = Field(default=None, max_length=1_000)
    version: str | None = Field(default=None, max_length=500)
    publisher: str | None = Field(default=None, max_length=1_000)
    install_location: Path | None = None
    installed: bool

    def canonical_digest(self) -> str:
        """Hash registration context and safe MSI product properties."""
        return canonical_digest(self.model_dump(mode="json"))


class ValidatedMsiProduct(FrozenModel):
    """Only product object accepted by the Stage 4D2A execution adapter."""

    product_code: str = Field(pattern=r"^\{[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}\}$")
    product_code_digest: str = Field(min_length=64, max_length=64)
    identity_digest: str = Field(min_length=64, max_length=64)
    metadata_digest: str = Field(min_length=64, max_length=64)
    capability_digest: str = Field(min_length=64, max_length=64)
    registration_digest: str = Field(min_length=64, max_length=64)
    install_context: MsiInstallContext
    display_name: str = Field(min_length=1, max_length=1_000)
    display_version: str | None = Field(default=None, max_length=500)
    publisher: str = Field(min_length=1, max_length=1_000)
    scope: SoftwareScope
    architecture: SoftwareArchitecture
    source_anchor_digest: str = Field(min_length=64, max_length=64)
    validated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def require_matching_product_code_digest(self) -> Self:
        """Reject a ProductCode substituted after validation."""
        if self.product_code_digest != canonical_digest(self.product_code):
            raise ValueError("Validated MSI ProductCode digest does not match")
        return self

    def canonical_digest(self) -> str:
        """Bind evidence and its observation time for one exact validated object."""
        return canonical_digest(self.model_dump(mode="json"))

    def evidence_digest(self) -> str:
        """Hash stable execution evidence while allowing a fresh validation timestamp."""
        return canonical_digest(self.model_dump(mode="json", exclude={"validated_at"}))


class MsiExecutionAssessment(FrozenModel):
    """Execution-specific policy result; Stage 4D1 Preview permission is insufficient."""

    decision: MsiExecutionDecision
    safety_class: SoftwareSafetyClass
    risk_level: RiskLevel
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]

    @model_validator(mode="after")
    def validate_risk_decision(self) -> Self:
        """Allow only R2/R2_HIGH and keep R3/R4 non-executable."""
        if self.decision is MsiExecutionDecision.ALLOW and self.risk_level not in {
            RiskLevel.R2,
            RiskLevel.R2_HIGH_IMPACT,
        }:
            raise ValueError("Executable MSI assessments must be R2")
        if self.risk_level is RiskLevel.R4:
            raise ValueError("R4 is prohibited rather than previewable")
        return self

    def canonical_digest(self) -> str:
        """Hash the exact execution permission and risk."""
        return canonical_digest(self.model_dump(mode="json"))


class RelatedProcessEvidence(FrozenModel):
    """Strong path-based relation to one running process; no command line is retained."""

    pid: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=500)
    executable_path_digest: str = Field(min_length=64, max_length=64)


class RelatedServiceEvidence(FrozenModel):
    """Strong path-based relation to one service without any control authority."""

    service_name: str = Field(min_length=1, max_length=500)
    display_name: str = Field(min_length=1, max_length=500)
    state: str = Field(min_length=1, max_length=100)
    executable_path_digest: str = Field(min_length=64, max_length=64)


class SoftwareExecutionPreflightResult(FrozenModel):
    """Read-only preflight evidence; it never closes a process or stops a service."""

    state: MsiPreflightState
    related_processes: tuple[RelatedProcessEvidence, ...] = ()
    related_services: tuple[RelatedServiceEvidence, ...] = ()
    process_probe_complete: bool
    service_probe_complete: bool
    installer_busy: bool | None = None
    reboot_pending: bool | None = None
    privilege_expected: str = Field(pattern=r"^(current_user|required|unknown)$")
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def canonical_digest(self) -> str:
        """Bind confirmation to all currently observed blockers and warnings."""
        return canonical_digest(self.model_dump(mode="json"))


class MsiUninstallPlan(FrozenModel):
    """Immutable single-product plan for one narrow registered MSI tool."""

    plan_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID = Field(default_factory=uuid4)
    operation_id: UUID = Field(default_factory=uuid4)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_goal: str = Field(min_length=1, max_length=2_000)
    target_query: SoftwareTargetQuery
    identity_digest: str = Field(min_length=64, max_length=64)
    validated_product_digest: str = Field(min_length=64, max_length=64)
    capability_digest: str = Field(min_length=64, max_length=64)
    execution_assessment_digest: str = Field(min_length=64, max_length=64)
    preflight_digest: str = Field(min_length=64, max_length=64)
    risk_level: RiskLevel
    tool_name: str = "software.uninstall.msi"
    max_items: int = Field(default=5_000, ge=1, le=20_000)
    requires_plan_confirmation: bool = True
    requires_runtime_confirmation: bool = True
    rollback_level: RollbackLevel = RollbackLevel.NONE

    @model_validator(mode="after")
    def validate_execution_contract(self) -> Self:
        """Keep the plan single-tool, irreversible, and double-confirmed."""
        if self.tool_name != "software.uninstall.msi":
            raise ValueError("Stage 4D2A has exactly one MSI uninstall tool")
        if self.risk_level not in {RiskLevel.R2, RiskLevel.R2_HIGH_IMPACT}:
            raise ValueError("MSI uninstall plans must be R2")
        if not self.requires_plan_confirmation or not self.requires_runtime_confirmation:
            raise ValueError("MSI uninstall requires both confirmations")
        if self.rollback_level is not RollbackLevel.NONE:
            raise ValueError("Software uninstall cannot claim automatic rollback")
        return self

    def canonical_digest(self) -> str:
        """Hash every field that can affect authorization."""
        return canonical_digest(self.model_dump(mode="json"))


class MsiUninstallPreview(FrozenModel):
    """Expiring object-specific execution Preview with truthful recovery limits."""

    preview_id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    transaction_id: UUID
    operation_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    target: NormalizedInstalledSoftware
    identity_digest: str = Field(min_length=64, max_length=64)
    validated_product: ValidatedMsiProduct
    capability: UninstallCapability
    execution_assessment: MsiExecutionAssessment
    preflight: SoftwareExecutionPreflightResult
    executable: bool
    rollback_level: RollbackLevel = RollbackLevel.NONE
    recovery_guidance: str = (
        "Recovery normally requires reinstalling the software. Reinstallation is not Undo and "
        "does not guarantee restoration of settings or user data."
    )

    @model_validator(mode="after")
    def bind_all_execution_evidence(self) -> Self:
        """Reject stale, blocked, mismatched, or falsely reversible Previews."""
        if self.expires_at <= self.generated_at:
            raise ValueError("MSI uninstall Preview must expire")
        if self.identity_digest != self.target.identity.canonical_digest():
            raise ValueError("MSI Preview identity mismatch")
        if self.identity_digest != self.validated_product.identity_digest:
            raise ValueError("Validated MSI identity mismatch")
        allowed = (
            self.execution_assessment.decision is MsiExecutionDecision.ALLOW
            and self.preflight.state is MsiPreflightState.READY
        )
        if self.executable != allowed:
            raise ValueError("MSI Preview executable flag contradicts policy or preflight")
        if self.rollback_level is not RollbackLevel.NONE:
            raise ValueError("MSI uninstall Preview cannot claim rollback")
        return self

    def invariant_digest(self) -> str:
        """Hash stable evidence that must survive fresh runtime Preview generation."""
        return canonical_digest(
            {
                "plan_id": self.plan_id,
                "transaction_id": self.transaction_id,
                "operation_id": self.operation_id,
                "plan_digest": self.plan_digest,
                "identity_digest": self.identity_digest,
                "validated_product_digest": self.validated_product.evidence_digest(),
                "capability_digest": self.capability.canonical_digest(),
                "assessment_digest": self.execution_assessment.canonical_digest(),
                "preflight_digest": self.preflight.canonical_digest(),
                "risk_level": self.execution_assessment.risk_level,
                "executable": self.executable,
            }
        )

    def canonical_digest(self) -> str:
        """Bind one confirmation to this exact generated Preview."""
        return canonical_digest(self.model_dump(mode="json"))


class MsiUninstallRequest(FrozenModel):
    """Exact internal request accepted by the sole Stage 4D2A write tool."""

    transaction_id: UUID
    operation_id: UUID
    plan_id: UUID
    preview_id: UUID
    product: ValidatedMsiProduct


class MsiInstallerExecutionResult(FrozenModel):
    """Observed fixed-client result before inventory verification."""

    category: MsiInstallerResultCategory
    exit_code: int | None = Field(default=None, ge=0)
    launched: bool
    cancellation_requested_before_launch: bool = False
    cancellation_requested_after_launch: bool = False
    long_running_observed: bool = False
    started_at: datetime | None = None
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: int = Field(default=0, ge=0)
    error_type: str | None = Field(default=None, max_length=200)


class MsiUninstallResult(FrozenModel):
    """Tool output; success is deliberately not the final uninstall verdict."""

    transaction_id: UUID
    operation_id: UUID
    identity_digest: str = Field(min_length=64, max_length=64)
    product_code_digest: str = Field(min_length=64, max_length=64)
    installer: MsiInstallerExecutionResult


class MsiResidualReport(FrozenModel):
    """Bounded read-only observation of the exact pre-known install location."""

    checked_location: bool
    install_location_present: bool | None = None
    reparse_or_symlink: bool | None = None
    warnings: tuple[str, ...] = ()
    deletion_performed: bool = False


class MsiUninstallVerification(FrozenModel):
    """Final verdict combining installer outcome with fresh local evidence."""

    state: MsiVerificationState
    original_identity_present: bool | None
    original_product_code_present: bool | None
    replacement_candidates: int = Field(default=0, ge=0, le=100)
    inventory_refreshed: bool
    evidence: tuple[str, ...]
    warnings: tuple[str, ...] = ()


class MsiUninstallExecutionReport(FrozenModel):
    """Final user-facing transaction report after verification and residual inspection."""

    transaction_id: UUID
    plan_id: UUID
    preview_id: UUID
    target_summary: str = Field(min_length=1, max_length=1_000)
    risk_level: RiskLevel
    installer: MsiInstallerExecutionResult
    verification: MsiUninstallVerification
    residual: MsiResidualReport
    rollback_level: RollbackLevel = RollbackLevel.NONE
    recovery_guidance: str = Field(min_length=1, max_length=2_000)
