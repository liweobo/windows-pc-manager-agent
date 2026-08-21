"""Provider-neutral contracts for controlled interactive vendor uninstallers."""

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


class VendorMetadataSourceKind(StrEnum):
    """Finite registry metadata sources recognized by Stage 4D2B."""

    INTERACTIVE_UNINSTALL_STRING = "interactive_uninstall_string"


class VendorParseConfidence(StrEnum):
    """Confidence that Windows argv parsing produced one unambiguous command."""

    HIGH = "high"
    LOW = "low"


class VendorArgumentDecision(StrEnum):
    """Whether exact parsed arguments fit the narrow interactive policy."""

    ALLOW = "allow"
    BLOCK = "block"


class VendorAuthenticodeStatus(StrEnum):
    """Offline Authenticode verification result."""

    VALID = "valid"
    INVALID = "invalid"
    UNKNOWN = "unknown"


class VendorPublisherMatch(StrEnum):
    """Conservative relation between installed-software and signer publishers."""

    MATCHED = "matched"
    MISMATCHED = "mismatched"
    UNKNOWN = "unknown"


class VendorInstallLocationRelation(StrEnum):
    """Relationship between the executable and the exact known install directory."""

    INSIDE_INSTALL_LOCATION = "inside_install_location"
    OUTSIDE_INSTALL_LOCATION = "outside_install_location"
    UNKNOWN = "unknown"


class VendorTrustDecision(StrEnum):
    """Deterministic executable trust decision."""

    TRUSTED_FOR_EXECUTION = "trusted_for_execution"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    BLOCKED = "blocked"


class VendorExecutionDecision(StrEnum):
    """Final software-plus-executable execution policy decision."""

    ALLOW = "allow"
    BLOCK = "block"


class VendorPreflightState(StrEnum):
    """Read-only process and service preflight state."""

    READY = "ready"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class VendorProcessResultCategory(StrEnum):
    """Observed vendor process result; it is never final uninstall success."""

    PROCESS_EXITED_ZERO = "process_exited_zero"
    PROCESS_EXITED_NONZERO = "process_exited_nonzero"
    LAUNCH_FAILED = "launch_failed"
    VENDOR_REQUESTED_ELEVATION = "vendor_requested_elevation"
    CANCELLED_BEFORE_LAUNCH = "cancelled_before_launch"
    STOPPED_MONITORING = "stopped_monitoring"
    MONITORING_DETACHED = "monitoring_detached"
    UNKNOWN = "unknown"


class VendorVerificationState(StrEnum):
    """Final observed software state after a fresh inventory."""

    VERIFIED_REMOVED = "verified_removed"
    COMPLETED_UNVERIFIED = "completed_unverified"
    REMOVED_WITH_UNEXPECTED_PROCESS_RESULT = "removed_with_unexpected_process_result"
    TARGET_INSTANCE_CHANGED = "target_instance_changed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class VendorUninstallTransactionState(StrEnum):
    """Durable vendor lifecycle with no automatic retry transition."""

    PREVIEWED = "previewed"
    AWAITING_PLAN_CONFIRMATION = "awaiting_plan_confirmation"
    PLAN_CONFIRMED = "plan_confirmed"
    VALIDATING = "validating"
    AWAITING_RUNTIME_CONFIRMATION = "awaiting_runtime_confirmation"
    DISPATCHING = "dispatching"
    EXECUTING = "executing"
    WAITING_FOR_VENDOR_UI = "waiting_for_vendor_ui"
    MONITORING = "monitoring"
    PROCESS_EXITED = "process_exited"
    VERIFYING = "verifying"
    VERIFIED_REMOVED = "verified_removed"
    COMPLETED_UNVERIFIED = "completed_unverified"
    STOPPED_MONITORING = "stopped_monitoring"
    USER_CANCELLED = "user_cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ParsedVendorUninstallMetadata(FrozenModel):
    """Structured parse of one untrusted interactive UninstallString."""

    source_digest: str = Field(min_length=64, max_length=64)
    executable_token: str = Field(min_length=1, max_length=32_768)
    raw_arguments: tuple[str, ...] = Field(default=(), max_length=32, repr=False)
    source_kind: VendorMetadataSourceKind = VendorMetadataSourceKind.INTERACTIVE_UNINSTALL_STRING
    parse_confidence: VendorParseConfidence
    warnings: tuple[str, ...] = ()

    def argument_fingerprint(self) -> str:
        """Hash exact argv tokens and ordering without logging their contents."""
        return canonical_digest(self.raw_arguments)


class VendorArgumentAssessment(FrozenModel):
    """Deterministic decision for the exact parsed argument vector."""

    decision: VendorArgumentDecision
    argument_fingerprint: str = Field(min_length=64, max_length=64)
    argument_count: int = Field(ge=0, le=32)
    reasons: tuple[str, ...]

    def canonical_digest(self) -> str:
        """Bind confirmation to the exact argument policy result."""
        return canonical_digest(self.model_dump(mode="json"))


class VendorExecutableFileIdentity(FrozenModel):
    """Handle-backed Windows identity plus content hash for one executable."""

    executable_path: Path
    volume_serial: int = Field(ge=0)
    file_id: str = Field(min_length=1, max_length=64)
    size_bytes: int = Field(ge=1)
    created_ns: int = Field(ge=0)
    modified_ns: int = Field(ge=0)
    attributes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def canonical_digest(self) -> str:
        """Hash path, stable file identity, metadata, and content hash."""
        return canonical_digest(self.model_dump(mode="json"))


class VendorAuthenticodeEvidence(FrozenModel):
    """Offline signature status and certificate display identity."""

    status: VendorAuthenticodeStatus
    signer_subject: str | None = Field(default=None, max_length=2_000)
    signer_organization: str | None = Field(default=None, max_length=1_000)
    error_code: str | None = Field(default=None, max_length=200)

    def canonical_digest(self) -> str:
        """Hash signature evidence without certificate or binary contents."""
        return canonical_digest(self.model_dump(mode="json"))


class VendorExecutableObservation(FrozenModel):
    """Read-only executable observation before deterministic trust policy."""

    file_identity: VendorExecutableFileIdentity
    local_fixed_volume: bool
    reparse_free: bool
    blocked_location: bool
    install_location_relation: VendorInstallLocationRelation
    authenticode: VendorAuthenticodeEvidence
    publisher_match: VendorPublisherMatch
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    warnings: tuple[str, ...] = ()

    def evidence_digest(self) -> str:
        """Hash stable evidence while allowing a fresh observation timestamp."""
        return canonical_digest(self.model_dump(mode="json", exclude={"observed_at"}))


class VendorExecutableTrustAssessment(FrozenModel):
    """Default-deny decision over path, file identity, signature, and publisher."""

    decision: VendorTrustDecision
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]

    def canonical_digest(self) -> str:
        """Hash the exact executable trust decision."""
        return canonical_digest(self.model_dump(mode="json"))


class VendorUninstallerIdentity(FrozenModel):
    """Exact executable and arguments authorized by a future confirmation."""

    software_identity_hash: str = Field(min_length=64, max_length=64)
    registry_source_fingerprint: str = Field(min_length=64, max_length=64)
    command_metadata_digest: str = Field(min_length=64, max_length=64)
    executable: VendorExecutableObservation
    arguments: tuple[str, ...] = Field(default=(), max_length=32, repr=False)
    argument_assessment: VendorArgumentAssessment
    trust: VendorExecutableTrustAssessment
    built_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_argument_binding(self) -> Self:
        """Reject an argument vector substituted after policy evaluation."""
        if self.argument_assessment.argument_fingerprint != canonical_digest(self.arguments):
            raise ValueError("Vendor argument fingerprint does not match")
        if self.argument_assessment.argument_count != len(self.arguments):
            raise ValueError("Vendor argument count does not match")
        return self

    def canonical_digest(self) -> str:
        """Hash every execution-relevant executable and argument field."""
        return canonical_digest(self.model_dump(mode="json"))

    def invariant_digest(self) -> str:
        """Hash stable identity while allowing fresh observation times."""
        return canonical_digest(
            {
                "software_identity_hash": self.software_identity_hash,
                "registry_source_fingerprint": self.registry_source_fingerprint,
                "command_metadata_digest": self.command_metadata_digest,
                "executable_evidence": self.executable.evidence_digest(),
                "arguments": self.arguments,
                "argument_assessment": self.argument_assessment,
                "trust": self.trust,
            }
        )


class VendorExecutionAssessment(FrozenModel):
    """Execution-specific policy result after Stage 4D1 safety classification."""

    decision: VendorExecutionDecision
    safety_class: SoftwareSafetyClass
    risk_level: RiskLevel
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]

    @model_validator(mode="after")
    def validate_allowed_risk(self) -> Self:
        """Permit execution only at the two explicitly approved R2 levels."""
        if self.decision is VendorExecutionDecision.ALLOW and self.risk_level not in {
            RiskLevel.R2,
            RiskLevel.R2_HIGH_IMPACT,
        }:
            raise ValueError("Executable Vendor assessments must be R2")
        if self.risk_level is RiskLevel.R4:
            raise ValueError("R4 cannot be represented as a Vendor Preview")
        return self

    def canonical_digest(self) -> str:
        """Hash software class, decision, risk, and reasons."""
        return canonical_digest(self.model_dump(mode="json"))


class VendorRelatedProcess(FrozenModel):
    """Read-only process relation; no control capability is represented."""

    pid: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=500)
    executable_path_digest: str = Field(min_length=64, max_length=64)


class VendorRelatedService(FrozenModel):
    """Read-only service relation; no stop capability is represented."""

    service_name: str = Field(min_length=1, max_length=500)
    display_name: str = Field(min_length=1, max_length=500)
    state: str = Field(min_length=1, max_length=100)
    executable_path_digest: str = Field(min_length=64, max_length=64)


class VendorExecutionPreflightResult(FrozenModel):
    """Complete process/service evidence with truthful warnings and blockers."""

    state: VendorPreflightState
    related_processes: tuple[VendorRelatedProcess, ...] = ()
    related_services: tuple[VendorRelatedService, ...] = ()
    process_probe_complete: bool
    service_probe_complete: bool
    active_uninstall_present: bool
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def canonical_digest(self) -> str:
        """Bind confirmation to every preflight result and warning."""
        return canonical_digest(self.model_dump(mode="json"))


class VendorUninstallPlan(FrozenModel):
    """Immutable one-object plan for the only Stage 4D2B write tool."""

    plan_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID = Field(default_factory=uuid4)
    operation_id: UUID = Field(default_factory=uuid4)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_goal: str = Field(min_length=1, max_length=2_000)
    target_query: SoftwareTargetQuery
    identity_digest: str = Field(min_length=64, max_length=64)
    capability_digest: str = Field(min_length=64, max_length=64)
    vendor_identity_digest: str = Field(min_length=64, max_length=64)
    execution_assessment_digest: str = Field(min_length=64, max_length=64)
    preflight_digest: str = Field(min_length=64, max_length=64)
    risk_level: RiskLevel
    tool_name: str = "software.uninstall.vendor"
    max_items: int = Field(default=5_000, ge=1, le=20_000)
    requires_plan_confirmation: bool = True
    requires_runtime_confirmation: bool = True
    rollback_level: RollbackLevel = RollbackLevel.NONE

    @model_validator(mode="after")
    def validate_execution_contract(self) -> Self:
        """Keep the plan single-tool, one-object, and non-reversible."""
        if self.tool_name != "software.uninstall.vendor":
            raise ValueError("Stage 4D2B has exactly one Vendor uninstall tool")
        if self.risk_level not in {RiskLevel.R2, RiskLevel.R2_HIGH_IMPACT}:
            raise ValueError("Vendor uninstall plans must be R2")
        if not self.requires_plan_confirmation or not self.requires_runtime_confirmation:
            raise ValueError("Vendor uninstall requires both confirmations")
        if self.rollback_level is not RollbackLevel.NONE:
            raise ValueError("Vendor uninstall cannot claim automatic rollback")
        return self

    def canonical_digest(self) -> str:
        """Hash every field that can affect authorization."""
        return canonical_digest(self.model_dump(mode="json"))


class VendorUninstallPreview(FrozenModel):
    """Expiring Preview bound to software, executable, arguments, and preflight."""

    preview_id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    transaction_id: UUID
    operation_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    target: NormalizedInstalledSoftware
    identity_digest: str = Field(min_length=64, max_length=64)
    capability: UninstallCapability
    vendor_identity: VendorUninstallerIdentity
    execution_assessment: VendorExecutionAssessment
    preflight: VendorExecutionPreflightResult
    executable: bool
    rollback_level: RollbackLevel = RollbackLevel.NONE
    recovery_guidance: str = (
        "Vendor software removal cannot be undone automatically. Reinstalling is a manual "
        "recovery option, not Undo, and may not restore settings or user data."
    )

    @model_validator(mode="after")
    def bind_all_execution_evidence(self) -> Self:
        """Reject stale, blocked, mismatched, or falsely reversible Previews."""
        if self.expires_at <= self.generated_at:
            raise ValueError("Vendor uninstall Preview must expire")
        if self.identity_digest != self.target.identity.canonical_digest():
            raise ValueError("Vendor Preview software identity mismatch")
        if self.identity_digest != self.vendor_identity.software_identity_hash:
            raise ValueError("Vendor executable software binding mismatch")
        allowed = (
            self.vendor_identity.trust.decision is VendorTrustDecision.TRUSTED_FOR_EXECUTION
            and self.vendor_identity.argument_assessment.decision is VendorArgumentDecision.ALLOW
            and self.execution_assessment.decision is VendorExecutionDecision.ALLOW
            and self.preflight.state is VendorPreflightState.READY
        )
        if self.executable != allowed:
            raise ValueError("Vendor Preview executable flag contradicts current evidence")
        if self.rollback_level is not RollbackLevel.NONE:
            raise ValueError("Vendor uninstall Preview cannot claim rollback")
        return self

    def invariant_digest(self) -> str:
        """Hash evidence that fresh runtime validation must reproduce exactly."""
        return canonical_digest(
            {
                "plan_id": self.plan_id,
                "transaction_id": self.transaction_id,
                "operation_id": self.operation_id,
                "plan_digest": self.plan_digest,
                "identity_digest": self.identity_digest,
                "capability_digest": self.capability.canonical_digest(),
                "vendor_identity_digest": self.vendor_identity.invariant_digest(),
                "execution_assessment": self.execution_assessment,
                "preflight": self.preflight,
                "executable": self.executable,
            }
        )

    def canonical_digest(self) -> str:
        """Bind one confirmation to this exact generated Preview."""
        return canonical_digest(self.model_dump(mode="json"))


class ValidatedVendorUninstallAction(FrozenModel):
    """Only executable action shape accepted by the narrow platform adapter."""

    software_identity_hash: str = Field(min_length=64, max_length=64)
    vendor_identity: VendorUninstallerIdentity
    transaction_id: UUID
    validated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def require_execution_ready_identity(self) -> Self:
        """Reject actions whose immutable trust or argument policy is not executable."""
        if self.software_identity_hash != self.vendor_identity.software_identity_hash:
            raise ValueError("Validated Vendor action software identity mismatch")
        if self.vendor_identity.trust.decision is not VendorTrustDecision.TRUSTED_FOR_EXECUTION:
            raise ValueError("Validated Vendor action requires trusted executable evidence")
        if self.vendor_identity.argument_assessment.decision is not VendorArgumentDecision.ALLOW:
            raise ValueError("Validated Vendor action requires allowed arguments")
        return self

    def canonical_digest(self) -> str:
        """Hash the exact action accepted by the tool and adapter."""
        return canonical_digest(self.model_dump(mode="json"))


class VendorUninstallRequest(FrozenModel):
    """Reference-bound request accepted by ``software.uninstall.vendor``."""

    transaction_id: UUID
    operation_id: UUID
    plan_id: UUID
    preview_id: UUID
    action: ValidatedVendorUninstallAction


class VendorProcessExecutionResult(FrozenModel):
    """Observed process outcome before fresh software verification."""

    category: VendorProcessResultCategory
    process_id: int | None = Field(default=None, ge=1)
    exit_code: int | None = None
    launched: bool
    cancellation_requested_before_launch: bool = False
    monitoring_stopped_after_launch: bool = False
    long_running_observed: bool = False
    tracked_child_count: int = Field(default=0, ge=0, le=32)
    started_at: datetime | None = None
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: int = Field(default=0, ge=0)
    error_type: str | None = Field(default=None, max_length=200)


class VendorUninstallResult(FrozenModel):
    """Tool output; process completion is not final uninstall success."""

    transaction_id: UUID
    operation_id: UUID
    identity_digest: str = Field(min_length=64, max_length=64)
    vendor_identity_digest: str = Field(min_length=64, max_length=64)
    process: VendorProcessExecutionResult


class VendorResidualReport(FrozenModel):
    """Non-deleting observation of the exact known install location."""

    checked_location: bool
    install_location_present: bool | None = None
    reparse_or_symlink: bool | None = None
    warnings: tuple[str, ...] = ()
    deletion_performed: bool = False


class VendorUninstallVerification(FrozenModel):
    """Fresh-inventory verdict kept separate from process exit evidence."""

    state: VendorVerificationState
    original_identity_present: bool | None
    replacement_candidates: int = Field(default=0, ge=0, le=100)
    inventory_refreshed: bool
    evidence: tuple[str, ...]
    warnings: tuple[str, ...] = ()


class VendorUninstallExecutionReport(FrozenModel):
    """Final user-facing report after monitoring and fresh verification."""

    transaction_id: UUID
    plan_id: UUID
    preview_id: UUID
    target_summary: str = Field(min_length=1, max_length=1_000)
    risk_level: RiskLevel
    process: VendorProcessExecutionResult
    verification: VendorUninstallVerification
    residual: VendorResidualReport
    rollback_level: RollbackLevel = RollbackLevel.NONE
    recovery_guidance: str = Field(min_length=1, max_length=2_000)
