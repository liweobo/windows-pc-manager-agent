"""Provider-neutral contracts for controlled current-user winget uninstall."""

from __future__ import annotations

import re
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
    canonical_digest,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareScope

OFFICIAL_WINGET_SOURCE_NAME = "winget"
OFFICIAL_WINGET_SOURCE_IDENTIFIER = "Microsoft.Winget.Source_8wekyb3d8bbwe"
DESKTOP_APP_INSTALLER_FAMILY = "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe"
_PACKAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,255}$")
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+() -]{0,199}$")


class WingetAvailabilityState(StrEnum):
    """Trusted executable discovery outcome."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNTRUSTED = "untrusted"


class WingetInventoryState(StrEnum):
    """Bounded package inventory outcome."""

    COMPLETE = "complete"
    FAILED = "failed"
    TRUNCATED = "truncated"


class WingetMappingConfidence(StrEnum):
    """Confidence of one Package-to-Software relationship."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class WingetCapabilityDecision(StrEnum):
    """Whether package evidence fits the Stage 4D2C1 mechanism boundary."""

    SUPPORTED = "supported"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"


class WingetExecutionDecision(StrEnum):
    """Final deterministic software safety decision."""

    ALLOW = "allow"
    BLOCK = "block"


class WingetPreflightState(StrEnum):
    """Read-only execution preflight state."""

    READY = "ready"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class WingetProcessResultCategory(StrEnum):
    """Observed package-manager process result, not uninstall success."""

    EXITED_ZERO = "exited_zero"
    EXITED_NONZERO = "exited_nonzero"
    LAUNCH_FAILED = "launch_failed"
    PRIVILEGE_REQUIRED = "privilege_required"
    REBOOT_REQUIRED = "reboot_required"
    CANCELLED_BEFORE_LAUNCH = "cancelled_before_launch"
    MONITORING_STOPPED = "monitoring_stopped"
    UNKNOWN = "unknown"


class WingetVerificationState(StrEnum):
    """Final verdict from fresh package and installed-software inventories."""

    VERIFIED_REMOVED = "verified_removed"
    PACKAGE_REMOVED_SOFTWARE_PRESENT = "package_removed_software_present"
    SOFTWARE_REMOVED_PACKAGE_UNKNOWN = "software_removed_package_unknown"
    PACKAGE_STILL_PRESENT = "package_still_present"
    TARGET_INSTANCE_CHANGED = "target_instance_changed"
    COMPLETED_UNVERIFIED = "completed_unverified"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class WingetUninstallTransactionState(StrEnum):
    """Durable single-dispatch lifecycle with no automatic retry."""

    PREVIEWED = "previewed"
    AWAITING_PLAN_CONFIRMATION = "awaiting_plan_confirmation"
    PLAN_CONFIRMED = "plan_confirmed"
    VALIDATING = "validating"
    AWAITING_RUNTIME_CONFIRMATION = "awaiting_runtime_confirmation"
    DISPATCHING = "dispatching"
    EXECUTING = "executing"
    MONITORING = "monitoring"
    PROCESS_EXITED = "process_exited"
    VERIFYING = "verifying"
    VERIFIED_REMOVED = "verified_removed"
    COMPLETED_UNVERIFIED = "completed_unverified"
    REBOOT_REQUIRED = "reboot_required"
    PRIVILEGE_REQUIRED = "privilege_required"
    CANCELLED = "cancelled"
    FAILED = "failed"
    BLOCKED = "blocked"
    INTERRUPTED = "interrupted"


class WingetExecutableIdentity(FrozenModel):
    """Exact App Execution Alias and App Installer package identity."""

    alias_path: Path
    package_full_name: str = Field(min_length=1, max_length=500)
    package_family_name: str = Field(min_length=1, max_length=200)
    target_executable: str = Field(min_length=1, max_length=260)
    reparse_tag: int = Field(ge=0)
    alias_size: int = Field(ge=0)
    alias_modified_ns: int = Field(ge=0)
    alias_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def require_desktop_app_installer(self) -> Self:
        """Reject aliases that do not resolve to Microsoft's App Installer family."""
        if self.package_family_name != DESKTOP_APP_INSTALLER_FAMILY:
            raise ValueError("winget alias must belong to Microsoft Desktop App Installer")
        if not self.package_full_name.startswith("Microsoft.DesktopAppInstaller_"):
            raise ValueError("unexpected App Installer package full name")
        if Path(self.target_executable).name.casefold() not in {
            "winget.exe",
            "appinstallercli.exe",
        }:
            raise ValueError("unexpected App Installer alias target")
        return self

    def invariant_digest(self) -> str:
        """Hash stable executable evidence while permitting a fresh observation time."""
        return canonical_digest(self.model_dump(mode="json", exclude={"observed_at"}))


class WingetAvailability(FrozenModel):
    """Fail-closed discovery result without PATH fallback."""

    state: WingetAvailabilityState
    executable: WingetExecutableIdentity | None = None
    reason: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def bind_state_to_identity(self) -> Self:
        """Require exact executable identity only for an available result."""
        if (self.state is WingetAvailabilityState.AVAILABLE) != (self.executable is not None):
            raise ValueError("winget availability contradicts executable identity")
        return self


class RawWingetPackage(FrozenModel):
    """Ephemeral package record parsed from a trusted export JSON document."""

    package_id: str = Field(min_length=1, max_length=256)
    installed_version: str = Field(min_length=1, max_length=200)
    source_name: str = Field(min_length=1, max_length=100)
    source_identifier: str = Field(min_length=1, max_length=200)
    scope: SoftwareScope = SoftwareScope.CURRENT_USER


class WingetPackageIdentity(FrozenModel):
    """Exact source-qualified package identity used for every write decision."""

    identity_version: int = Field(default=1, ge=1, le=1)
    package_id: str = Field(min_length=1, max_length=256)
    installed_version: str = Field(min_length=1, max_length=200)
    source_name: str = Field(min_length=1, max_length=100)
    source_identifier: str = Field(min_length=1, max_length=200)
    scope: SoftwareScope

    @model_validator(mode="after")
    def require_narrow_identity(self) -> Self:
        """Allow only safe IDs, official community source, and current-user scope."""
        if _PACKAGE_ID_PATTERN.fullmatch(self.package_id) is None:
            raise ValueError("package ID contains unsupported characters")
        if _VERSION_PATTERN.fullmatch(self.installed_version) is None:
            raise ValueError("installed version contains unsupported characters")
        if self.source_name != OFFICIAL_WINGET_SOURCE_NAME:
            raise ValueError("only the official winget source is supported")
        if self.source_identifier != OFFICIAL_WINGET_SOURCE_IDENTIFIER:
            raise ValueError("unrecognized winget source identity")
        if self.scope is not SoftwareScope.CURRENT_USER:
            raise ValueError("only current-user winget packages are supported")
        return self

    def canonical_digest(self) -> str:
        """Hash Package ID, version, source, and scope."""
        return canonical_digest(self.model_dump(mode="json"))


class NormalizedWingetPackage(FrozenModel):
    """Safe package projection; no repository URL or free-form command is retained."""

    identity: WingetPackageIdentity
    package_id: str = Field(min_length=1, max_length=256)
    installed_version: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def bind_visible_fields(self) -> Self:
        """Keep visible values identical to the execution identity."""
        if self.package_id != self.identity.package_id:
            raise ValueError("visible package ID does not match identity")
        if self.installed_version != self.identity.installed_version:
            raise ValueError("visible package version does not match identity")
        return self


class WingetPackageInventory(FrozenModel):
    """Bounded, partial-aware official-source package inventory."""

    state: WingetInventoryState
    packages: tuple[NormalizedWingetPackage, ...]
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    warnings: tuple[str, ...] = ()


class WingetPackageQuery(FrozenModel):
    """Exact identity or explicit Package ID selector."""

    identity_digest: str | None = Field(default=None, min_length=64, max_length=64)
    package_id: str | None = Field(default=None, min_length=1, max_length=256)
    installed_version: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_selector(self) -> Self:
        """Reject name-only and unbounded resolution."""
        if self.identity_digest is None and self.package_id is None:
            raise ValueError("winget target requires identity digest or Package ID")
        if self.package_id is not None and _PACKAGE_ID_PATTERN.fullmatch(self.package_id) is None:
            raise ValueError("package ID contains unsupported characters")
        return self


class ResolvedWingetPackage(FrozenModel):
    """Exact selection or explicit ambiguity; candidates are never auto-selected."""

    query: WingetPackageQuery
    selected: NormalizedWingetPackage | None = None
    candidates: tuple[NormalizedWingetPackage, ...] = Field(default=(), max_length=100)
    ambiguous: bool
    reason: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_resolution(self) -> Self:
        """Require one exact selection or an explicitly unresolved result."""
        if self.selected is not None and (self.ambiguous or self.candidates):
            raise ValueError("selected winget package cannot also be ambiguous")
        if self.selected is None and not self.ambiguous:
            raise ValueError("unresolved winget package must be marked ambiguous")
        return self


class WingetSoftwareMapping(FrozenModel):
    """Evidence linking one package identity to one installed-software identity."""

    package_identity_digest: str = Field(min_length=64, max_length=64)
    software_identity_digest: str | None = Field(default=None, min_length=64, max_length=64)
    confidence: WingetMappingConfidence
    evidence: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def executable(self) -> bool:
        """Return whether the mapping is singular and high confidence."""
        return (
            self.confidence is WingetMappingConfidence.HIGH
            and self.software_identity_digest is not None
        )

    def canonical_digest(self) -> str:
        """Hash exact package/software mapping evidence."""
        return canonical_digest(self.model_dump(mode="json"))


class WingetCapabilityAssessment(FrozenModel):
    """Package mechanism support independent of software safety class."""

    decision: WingetCapabilityDecision
    reasons: tuple[str, ...]
    package_identity_digest: str = Field(min_length=64, max_length=64)
    executable_identity_digest: str | None = Field(default=None, min_length=64, max_length=64)

    def canonical_digest(self) -> str:
        """Hash exact capability facts."""
        return canonical_digest(self.model_dump(mode="json"))


class WingetExecutionAssessment(FrozenModel):
    """Software classification and final execution decision."""

    decision: WingetExecutionDecision
    safety_class: SoftwareSafetyClass
    risk_level: RiskLevel
    reasons: tuple[str, ...]
    evidence: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_r2_for_allow(self) -> Self:
        """Allow writes only at one of the explicitly reviewed R2 levels."""
        if self.decision is WingetExecutionDecision.ALLOW and self.risk_level not in {
            RiskLevel.R2,
            RiskLevel.R2_HIGH_IMPACT,
        }:
            raise ValueError("allowed winget uninstall must be R2")
        if self.risk_level in {RiskLevel.R3, RiskLevel.R4}:
            raise ValueError("R3/R4 cannot enter a winget execution Preview")
        return self

    def canonical_digest(self) -> str:
        """Hash safety class, risk, and reasons."""
        return canonical_digest(self.model_dump(mode="json"))


class WingetRelatedProcess(FrozenModel):
    """Read-only process relation; no control authority is represented."""

    pid: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=500)
    executable_path_digest: str = Field(min_length=64, max_length=64)


class WingetRelatedService(FrozenModel):
    """Read-only service relation; running services are execution blockers."""

    service_name: str = Field(min_length=1, max_length=500)
    display_name: str = Field(min_length=1, max_length=500)
    state: str = Field(min_length=1, max_length=100)


class WingetExecutionPreflight(FrozenModel):
    """Complete process, service, package-manager, and concurrency evidence."""

    state: WingetPreflightState
    related_processes: tuple[WingetRelatedProcess, ...] = ()
    related_services: tuple[WingetRelatedService, ...] = ()
    process_probe_complete: bool
    service_probe_complete: bool
    winget_busy: bool
    another_uninstall_active: bool
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def canonical_digest(self) -> str:
        """Hash all warnings, blockers, and completeness flags."""
        return canonical_digest(self.model_dump(mode="json"))


class WingetUninstallPlan(FrozenModel):
    """Immutable one-package plan for the sole Stage 4D2C1 write tool."""

    plan_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID = Field(default_factory=uuid4)
    operation_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    version: int = Field(default=1, ge=1)
    user_goal: str = Field(min_length=1, max_length=2_000)
    package_query: WingetPackageQuery
    package_identity_digest: str = Field(min_length=64, max_length=64)
    software_identity_digest: str = Field(min_length=64, max_length=64)
    mapping_digest: str = Field(min_length=64, max_length=64)
    capability_digest: str = Field(min_length=64, max_length=64)
    executable_identity_digest: str = Field(min_length=64, max_length=64)
    safety_digest: str = Field(min_length=64, max_length=64)
    preflight_digest: str = Field(min_length=64, max_length=64)
    risk_level: RiskLevel
    tool_name: str = "software.uninstall.winget"
    requires_plan_confirmation: bool = True
    requires_runtime_confirmation: bool = True
    rollback_level: RollbackLevel = RollbackLevel.NONE

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        """Keep the plan single-tool, R2, twice-confirmed, and irreversible."""
        if self.tool_name != "software.uninstall.winget":
            raise ValueError("Stage 4D2C1 has exactly one write tool")
        if self.risk_level not in {RiskLevel.R2, RiskLevel.R2_HIGH_IMPACT}:
            raise ValueError("winget uninstall plans must be R2")
        if not self.requires_plan_confirmation or not self.requires_runtime_confirmation:
            raise ValueError("winget uninstall requires both confirmations")
        if self.rollback_level is not RollbackLevel.NONE:
            raise ValueError("winget uninstall cannot claim automatic rollback")
        return self

    def canonical_digest(self) -> str:
        """Hash every plan field that affects authorization."""
        return canonical_digest(self.model_dump(mode="json"))


class WingetUninstallPreview(FrozenModel):
    """Expiring Preview bound to package, software, executable, and preflight evidence."""

    preview_id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    transaction_id: UUID
    operation_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    package: NormalizedWingetPackage
    software: NormalizedInstalledSoftware
    mapping: WingetSoftwareMapping
    availability: WingetAvailability
    capability: WingetCapabilityAssessment
    execution_assessment: WingetExecutionAssessment
    preflight: WingetExecutionPreflight
    executable: bool
    rollback_level: RollbackLevel = RollbackLevel.NONE
    recovery_guidance: str = (
        "winget removal cannot be undone automatically. Reinstall is manual recovery, not Undo, "
        "and may not restore settings or user data."
    )

    @model_validator(mode="after")
    def bind_evidence(self) -> Self:
        """Reject mismatched, stale, blocked, or falsely reversible Preview data."""
        if self.expires_at <= self.generated_at:
            raise ValueError("winget Preview must expire")
        if self.mapping.package_identity_digest != self.package.identity.canonical_digest():
            raise ValueError("winget package mapping mismatch")
        if self.mapping.software_identity_digest != self.software.identity.canonical_digest():
            raise ValueError("winget software mapping mismatch")
        allowed = (
            self.mapping.executable
            and self.availability.state is WingetAvailabilityState.AVAILABLE
            and self.capability.decision is WingetCapabilityDecision.SUPPORTED
            and self.execution_assessment.decision is WingetExecutionDecision.ALLOW
            and self.preflight.state is WingetPreflightState.READY
        )
        if self.executable != allowed:
            raise ValueError("winget Preview executable flag contradicts evidence")
        if self.rollback_level is not RollbackLevel.NONE:
            raise ValueError("winget uninstall cannot claim rollback")
        return self

    def invariant_digest(self) -> str:
        """Hash the evidence a fresh runtime validation must reproduce."""
        executable = self.availability.executable
        return canonical_digest(
            {
                "plan_id": self.plan_id,
                "transaction_id": self.transaction_id,
                "operation_id": self.operation_id,
                "plan_digest": self.plan_digest,
                "package": self.package.identity,
                "software_identity": self.software.identity.canonical_digest(),
                "mapping": self.mapping,
                "executable_identity": (
                    executable.invariant_digest() if executable is not None else None
                ),
                "capability": self.capability,
                "execution_assessment": self.execution_assessment,
                "preflight": self.preflight,
                "executable": self.executable,
            }
        )

    def canonical_digest(self) -> str:
        """Bind confirmation to this exact generated Preview."""
        return canonical_digest(self.model_dump(mode="json"))


class ValidatedWingetUninstallAction(FrozenModel):
    """Only executable action shape accepted by the platform adapter."""

    transaction_id: UUID
    package_identity: WingetPackageIdentity
    software_identity_digest: str = Field(min_length=64, max_length=64)
    executable_identity: WingetExecutableIdentity
    validated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def canonical_digest(self) -> str:
        """Hash the exact adapter action without adding free-form arguments."""
        return canonical_digest(self.model_dump(mode="json"))


class WingetUninstallRequest(FrozenModel):
    """Reference-bound request accepted by ``software.uninstall.winget``."""

    transaction_id: UUID
    operation_id: UUID
    plan_id: UUID
    preview_id: UUID
    action: ValidatedWingetUninstallAction


class WingetProcessExecutionResult(FrozenModel):
    """Observed winget process evidence before dual inventory verification."""

    category: WingetProcessResultCategory
    launched: bool
    process_id: int | None = Field(default=None, ge=1)
    exit_code: int | None = None
    cancellation_requested_before_launch: bool = False
    monitoring_stopped_after_launch: bool = False
    started_at: datetime | None = None
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: int = Field(default=0, ge=0)
    error_type: str | None = Field(default=None, max_length=200)


class WingetUninstallResult(FrozenModel):
    """Tool result; it deliberately does not claim removal success."""

    transaction_id: UUID
    operation_id: UUID
    package_identity_digest: str = Field(min_length=64, max_length=64)
    software_identity_digest: str = Field(min_length=64, max_length=64)
    process: WingetProcessExecutionResult


class WingetResidualReport(FrozenModel):
    """Exact-path-only residual observation with no enumeration or deletion."""

    checked_location: bool
    install_location_present: bool | None = None
    reparse_or_symlink: bool | None = None
    warnings: tuple[str, ...] = ()
    deletion_performed: bool = False


class WingetUninstallVerification(FrozenModel):
    """Dual package/software inventory verdict separate from process exit."""

    state: WingetVerificationState
    package_inventory_refreshed: bool
    software_inventory_refreshed: bool
    original_package_present: bool | None
    original_software_present: bool | None
    evidence: tuple[str, ...]
    warnings: tuple[str, ...] = ()


class WingetUninstallExecutionReport(FrozenModel):
    """User-facing final report with truthful rollback and verification limits."""

    transaction_id: UUID
    plan_id: UUID
    preview_id: UUID
    target_summary: str = Field(min_length=1, max_length=1_000)
    risk_level: RiskLevel
    process: WingetProcessExecutionResult
    verification: WingetUninstallVerification
    residual: WingetResidualReport
    recovery_guidance: str = Field(min_length=1, max_length=2_000)


def fixed_winget_uninstall_arguments(identity: WingetPackageIdentity) -> tuple[str, ...]:
    """Return the sole finite argument vector; callers cannot add or replace flags."""
    return (
        "uninstall",
        "--id",
        identity.package_id,
        "--exact",
        "--source",
        OFFICIAL_WINGET_SOURCE_NAME,
        "--version",
        identity.installed_version,
        "--scope",
        "user",
        "--interactive",
        "--disable-interactivity",
    )
