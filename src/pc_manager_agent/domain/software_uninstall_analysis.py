"""Provider-neutral models for zero-execution software uninstall analysis."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, model_validator

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.system_diagnostics import SoftwareArchitecture, SoftwareScope


def canonical_digest(payload: object) -> str:
    """Return a deterministic SHA-256 digest for JSON-compatible evidence."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8", errors="surrogatepass")
    return hashlib.sha256(encoded).hexdigest()


class SoftwareSource(StrEnum):
    """Finite installed-software metadata sources understood by Stage 4D1."""

    MSI = "msi"
    REGISTRY = "registry"
    VENDOR = "vendor"
    PACKAGE_MANAGER = "package_manager"
    MSIX = "msix"
    PORTABLE = "portable"
    WINDOWS_FEATURE = "windows_feature"
    DRIVER_PACKAGE = "driver_package"
    UNKNOWN = "unknown"


class RegistryHive(StrEnum):
    """Allowed uninstall-registry hives."""

    CURRENT_USER = "HKEY_CURRENT_USER"
    LOCAL_MACHINE = "HKEY_LOCAL_MACHINE"


class RegistryView(StrEnum):
    """Registry view used to read an entry."""

    NATIVE = "native"
    X86 = "x86"
    X64 = "x64"


class SoftwareSafetyClass(StrEnum):
    """Deterministic protection class for a possible future removal."""

    USER_APPLICATION = "user_application"
    DEVELOPER_TOOL = "developer_tool"
    DEVELOPER_RUNTIME = "developer_runtime"
    SHARED_RUNTIME = "shared_runtime"
    DATABASE_SERVER = "database_server"
    BACKGROUND_PLATFORM = "background_platform"
    DEVICE_DRIVER = "device_driver"
    HARDWARE_UTILITY = "hardware_utility"
    WINDOWS_COMPONENT = "windows_component"
    WINDOWS_FEATURE = "windows_feature"
    SECURITY_SOFTWARE = "security_software"
    VPN_OR_NETWORK_COMPONENT = "vpn_or_network_component"
    AGENT_COMPONENT = "agent_component"
    ENTERPRISE_MANAGED = "enterprise_managed"
    PACKAGE_MANAGER = "package_manager"
    UNKNOWN = "unknown"


class SoftwareSafetyDecision(StrEnum):
    """Whether Stage 4D1 may describe a target in a read-only Preview."""

    PREVIEW_ALLOWED = "preview_allowed"
    PREVIEW_HIGH_IMPACT = "preview_high_impact"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"


class UninstallCapabilityType(StrEnum):
    """Mechanism indicated by metadata, never an executable instruction."""

    MSI = "msi"
    VENDOR_UNINSTALLER = "vendor_uninstaller"
    PACKAGE_MANAGER = "package_manager"
    MSIX = "msix"
    PORTABLE = "portable"
    WINDOWS_COMPONENT = "windows_component"
    DRIVER_PACKAGE = "driver_package"
    MULTIPLE_CAPABILITIES = "multiple_capabilities"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class CapabilitySupport(StrEnum):
    """Confidence that metadata identifies a single future mechanism."""

    METADATA_SUPPORTED = "metadata_supported"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"


class EvidenceKind(StrEnum):
    """Separate direct local evidence from conservative heuristic warnings."""

    KNOWN_EVIDENCE = "known_evidence"
    HEURISTIC_WARNING = "heuristic_warning"


class ImpactSeverity(StrEnum):
    """Non-alarmist impact severity used in the Preview."""

    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    BLOCKING = "blocking"


class TargetAcknowledgementState(StrEnum):
    """Lifecycle for a non-authorizing target-understanding acknowledgement."""

    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    EXPIRED = "expired"


class RawInstalledSoftwareEntry(FrozenModel):
    """Ephemeral, untrusted source record; raw commands are excluded from serialization."""

    raw_source_id: str = Field(min_length=1, max_length=4_096)
    source: SoftwareSource
    display_name: str | None = Field(default=None, max_length=1_000)
    display_version: str | None = Field(default=None, max_length=500)
    publisher: str | None = Field(default=None, max_length=1_000)
    install_location: Path | None = None
    install_date: str | None = Field(default=None, max_length=100)
    estimated_size_bytes: int | None = Field(default=None, ge=0)
    scope: SoftwareScope
    architecture: SoftwareArchitecture
    registry_hive: RegistryHive | None = None
    registry_view: RegistryView | None = None
    registry_key: str | None = Field(default=None, max_length=4_096)
    product_code: str | None = Field(default=None, max_length=100)
    windows_installer: bool | None = None
    system_component: bool | None = None
    package_manager_id: str | None = Field(default=None, max_length=500)
    package_id: str | None = Field(default=None, max_length=1_000)
    package_family_name: str | None = Field(default=None, max_length=1_000)
    package_full_name: str | None = Field(default=None, max_length=2_000)
    package_publisher_id: str | None = Field(default=None, max_length=1_000)
    uninstall_string: str | None = Field(default=None, exclude=True, repr=False, max_length=32_768)
    quiet_uninstall_string: str | None = Field(
        default=None,
        exclude=True,
        repr=False,
        max_length=32_768,
    )

    def source_anchor_digest(self) -> str:
        """Hash only stable source anchors, never raw uninstall command text."""
        return canonical_digest(
            {
                "source": self.source,
                "raw_source_id": self.raw_source_id,
                "scope": self.scope,
                "architecture": self.architecture,
                "registry_hive": self.registry_hive,
                "registry_view": self.registry_view,
                "registry_key": self.registry_key,
                "product_code": self.product_code,
                "package_manager_id": self.package_manager_id,
                "package_id": self.package_id,
                "package_family_name": self.package_family_name,
                "package_full_name": self.package_full_name,
            }
        )

    def command_metadata_digest(self) -> str:
        """Fingerprint command metadata without exposing or persisting its contents."""
        return canonical_digest(
            {
                "uninstall": self.uninstall_string,
                "quiet": self.quiet_uninstall_string,
            }
        )


class SoftwareIdentity(FrozenModel):
    """Stable source-qualified identity used for selection and stale-state detection."""

    identity_version: int = Field(default=1, ge=1, le=1)
    source: SoftwareSource
    scope: SoftwareScope
    architecture: SoftwareArchitecture
    display_name: str = Field(min_length=1, max_length=1_000)
    display_version: str | None = Field(default=None, max_length=500)
    publisher: str | None = Field(default=None, max_length=1_000)
    source_anchor_digest: str = Field(min_length=64, max_length=64)
    product_code: str | None = Field(default=None, max_length=100)
    package_manager_id: str | None = Field(default=None, max_length=500)
    package_id: str | None = Field(default=None, max_length=1_000)
    package_family_name: str | None = Field(default=None, max_length=1_000)
    package_full_name: str | None = Field(default=None, max_length=2_000)

    def canonical_digest(self) -> str:
        """Bind source anchors and visible identity fields into one stable digest."""
        return canonical_digest(self.model_dump(mode="json"))


class NormalizedInstalledSoftware(FrozenModel):
    """Safe UI/tool projection of one conservatively normalized source record."""

    identity: SoftwareIdentity
    display_name: str = Field(min_length=1, max_length=1_000)
    display_version: str | None = Field(default=None, max_length=500)
    publisher: str | None = Field(default=None, max_length=1_000)
    install_location: Path | None = None
    install_date: str | None = Field(default=None, max_length=100)
    estimated_size_bytes: int | None = Field(default=None, ge=0)
    source: SoftwareSource
    scope: SoftwareScope
    architecture: SoftwareArchitecture
    windows_installer: bool | None = None
    system_component: bool | None = None
    uninstall_metadata_present: bool = False
    quiet_uninstall_metadata_present: bool = False
    command_metadata_digest: str = Field(min_length=64, max_length=64)
    metadata_warnings: tuple[str, ...] = ()

    def metadata_digest(self) -> str:
        """Hash all safe fields that can alter classification or Preview content."""
        return canonical_digest(self.model_dump(mode="json"))


class SoftwareInventory(FrozenModel):
    """Bounded current software inventory with transparent partial-result metadata."""

    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    entries: tuple[NormalizedInstalledSoftware, ...]
    warnings: tuple[str, ...] = ()
    truncated: bool = False
    source_counts: dict[str, int] = Field(default_factory=dict)


class SoftwareTargetQuery(FrozenModel):
    """User-selected identity or conservative display-name search fields."""

    identity_digest: str | None = Field(default=None, min_length=64, max_length=64)
    display_name: str | None = Field(default=None, min_length=1, max_length=1_000)
    publisher: str | None = Field(default=None, min_length=1, max_length=1_000)
    display_version: str | None = Field(default=None, min_length=1, max_length=500)
    scope: SoftwareScope | None = None
    architecture: SoftwareArchitecture | None = None

    @model_validator(mode="after")
    def require_selector(self) -> Self:
        """Reject an unbounded target query."""
        if self.identity_digest is None and self.display_name is None:
            raise ValueError("Software target requires an identity digest or display name")
        return self


class ResolvedSoftwareTarget(FrozenModel):
    """Exact selection or bounded candidate set; ambiguity is never auto-resolved."""

    query: SoftwareTargetQuery
    selected: NormalizedInstalledSoftware | None = None
    candidates: tuple[NormalizedInstalledSoftware, ...] = Field(default=(), max_length=100)
    ambiguous: bool = False
    reason: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_resolution(self) -> Self:
        """Require exactly one selected item or an explicit unresolved candidate result."""
        if self.selected is not None and (self.ambiguous or self.candidates):
            raise ValueError("A selected software target cannot also be ambiguous")
        if self.selected is None and not self.ambiguous:
            raise ValueError("An unresolved software target must be marked ambiguous")
        return self


class ParsedUninstallMetadata(FrozenModel):
    """Sanitized result of parsing one untrusted Windows command line."""

    source_digest: str = Field(min_length=64, max_length=64)
    parsed: bool
    executable_path: Path | None = None
    executable_exists: bool = False
    executable_is_absolute: bool = False
    executable_is_unc: bool = False
    wrapper_detected: bool = False
    recognized_switches: tuple[str, ...] = ()
    argument_count: int = Field(default=0, ge=0, le=1_024)
    warnings: tuple[str, ...] = ()


class UninstallCapability(FrozenModel):
    """Read-only classification of metadata indicating a possible future mechanism."""

    capability_type: UninstallCapabilityType
    support: CapabilitySupport
    confidence: str = Field(pattern=r"^(low|medium|high)$")
    evidence: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    parsed_metadata: ParsedUninstallMetadata | None = None
    product_code: str | None = Field(default=None, max_length=100)
    package_manager_id: str | None = Field(default=None, max_length=500)
    package_id: str | None = Field(default=None, max_length=1_000)
    package_family_name: str | None = Field(default=None, max_length=1_000)

    def canonical_digest(self) -> str:
        """Bind the mechanism decision and sanitized evidence into the Preview."""
        return canonical_digest(self.model_dump(mode="json"))


class SoftwareSafetyAssessment(FrozenModel):
    """Deterministic classification and Preview-only decision."""

    safety_class: SoftwareSafetyClass
    decision: SoftwareSafetyDecision
    evidence: tuple[str, ...]
    reasons: tuple[str, ...]

    def canonical_digest(self) -> str:
        """Hash the exact safety decision and its evidence."""
        return canonical_digest(self.model_dump(mode="json"))


class SoftwareImpactFinding(FrozenModel):
    """One direct observation or explicitly labelled heuristic warning."""

    code: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    kind: EvidenceKind
    severity: ImpactSeverity
    title: str = Field(min_length=1, max_length=300)
    explanation: str = Field(min_length=1, max_length=2_000)
    evidence: dict[str, JsonValue] = Field(default_factory=dict)


class SoftwareImpactAssessment(FrozenModel):
    """Bounded, partial-aware impact analysis without a claimed dependency graph."""

    findings: tuple[SoftwareImpactFinding, ...]
    process_probe_complete: bool
    startup_probe_complete: bool
    service_probe_complete: bool
    warnings: tuple[str, ...] = ()
    disclaimer: str = (
        "No observed relationship does not prove that no dependency exists. "
        "Stage 4D1 does not build a complete software dependency graph."
    )

    def canonical_digest(self) -> str:
        """Hash all findings and completeness flags."""
        return canonical_digest(self.model_dump(mode="json"))


class SoftwareUninstallAnalysisPlan(FrozenModel):
    """Immutable R0 plan that can only prepare a non-executable Preview."""

    plan_id: UUID = Field(default_factory=uuid4)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_goal: str = Field(min_length=1, max_length=2_000)
    summary: str = Field(min_length=1, max_length=500)
    target_query: SoftwareTargetQuery
    tool_names: tuple[str, ...] = Field(min_length=1, max_length=5)
    max_items: int = Field(default=5_000, ge=1, le=20_000)
    risk_level: RiskLevel = RiskLevel.R0
    read_only: bool = True
    requires_plan_confirmation: bool = True
    requires_runtime_confirmation: bool = False
    rollback_level: RollbackLevel = RollbackLevel.NONE
    estimated_system_changes: int = Field(default=0, ge=0, le=0)

    @model_validator(mode="after")
    def validate_zero_execution_contract(self) -> Self:
        """Reject plans that imply mutation, runtime confirmation, or duplicate tools."""
        if self.risk_level is not RiskLevel.R0 or not self.read_only:
            raise ValueError("Stage 4D1 plans must be read-only R0")
        if not self.requires_plan_confirmation or self.requires_runtime_confirmation:
            raise ValueError("Stage 4D1 requires only an R0 plan confirmation")
        if self.rollback_level is not RollbackLevel.NONE or self.estimated_system_changes != 0:
            raise ValueError("Stage 4D1 cannot describe system changes or rollback work")
        if len(set(self.tool_names)) != len(self.tool_names):
            raise ValueError("Stage 4D1 tool names must be unique")
        return self

    def canonical_digest(self) -> str:
        """Bind plan confirmation to every analysis-relevant field."""
        return canonical_digest(self.model_dump(mode="json"))


class SoftwareUninstallPreview(FrozenModel):
    """Expiring analysis result that is structurally incapable of authorizing execution."""

    preview_id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    target: NormalizedInstalledSoftware
    identity_digest: str = Field(min_length=64, max_length=64)
    metadata_digest: str = Field(min_length=64, max_length=64)
    capability: UninstallCapability
    capability_digest: str = Field(min_length=64, max_length=64)
    safety: SoftwareSafetyAssessment
    impact: SoftwareImpactAssessment
    executable_in_current_stage: bool = False
    execution_performed: bool = False
    analysis_risk_level: RiskLevel = RiskLevel.R0
    analysis_rollback_level: RollbackLevel = RollbackLevel.NONE
    future_recovery_level: RollbackLevel = RollbackLevel.MANUAL
    blocked: bool
    stop_reason: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def enforce_zero_execution(self) -> Self:
        """Keep Stage 4D1 Preview non-executable under all constructor inputs."""
        if self.executable_in_current_stage or self.execution_performed:
            raise ValueError("Stage 4D1 Preview can never execute an uninstall")
        if self.analysis_risk_level is not RiskLevel.R0:
            raise ValueError("Stage 4D1 analysis risk must remain R0")
        if self.analysis_rollback_level is not RollbackLevel.NONE:
            raise ValueError("Read-only analysis requires no rollback")
        if self.expires_at <= self.generated_at:
            raise ValueError("Software Preview must expire after it is generated")
        if self.identity_digest != self.target.identity.canonical_digest():
            raise ValueError("Preview identity digest does not match its target")
        if self.metadata_digest != self.target.metadata_digest():
            raise ValueError("Preview metadata digest does not match its target")
        if self.capability_digest != self.capability.canonical_digest():
            raise ValueError("Preview capability digest does not match its capability")
        return self

    def canonical_digest(self) -> str:
        """Bind acknowledgement to the full immutable Preview."""
        return canonical_digest(self.model_dump(mode="json"))


class SoftwareTargetAcknowledgement(FrozenModel):
    """User understanding record that deliberately contains no execution capability."""

    acknowledgement_id: UUID = Field(default_factory=uuid4)
    preview_id: UUID
    preview_digest: str = Field(min_length=64, max_length=64)
    identity_digest: str = Field(min_length=64, max_length=64)
    object_summary: str = Field(min_length=1, max_length=1_000)
    expires_at: datetime
    state: TargetAcknowledgementState = TargetAcknowledgementState.PENDING


class SoftwareInventoryRequest(FrozenModel):
    """Bounded input for the software inventory tool."""

    max_items: int = Field(default=5_000, ge=1, le=20_000)


class SoftwareInventoryResult(FrozenModel):
    """Declared result for the software inventory tool."""

    inventory: SoftwareInventory
    execution_performed: bool = False


class SoftwareResolveRequest(FrozenModel):
    """Target query and inventory bound for deterministic resolution."""

    query: SoftwareTargetQuery
    max_items: int = Field(default=5_000, ge=1, le=20_000)


class SoftwareResolveResult(FrozenModel):
    """Declared result for target resolution."""

    resolved: ResolvedSoftwareTarget
    inventory_collected_at: datetime
    execution_performed: bool = False


class SoftwareInspectRequest(FrozenModel):
    """Exact identity lookup request used for fresh revalidation."""

    identity_digest: str = Field(min_length=64, max_length=64)
    max_items: int = Field(default=5_000, ge=1, le=20_000)


class SoftwareInspectResult(FrozenModel):
    """Fresh exact observation or explicit disappearance result."""

    software: NormalizedInstalledSoftware | None
    found: bool
    execution_performed: bool = False


class SoftwareCapabilityRequest(FrozenModel):
    """Exact target identity for local capability analysis."""

    identity_digest: str = Field(min_length=64, max_length=64)
    max_items: int = Field(default=5_000, ge=1, le=20_000)


class SoftwareCapabilityResult(FrozenModel):
    """Sanitized capability metadata for one fresh target."""

    identity_digest: str = Field(min_length=64, max_length=64)
    capability: UninstallCapability
    execution_performed: bool = False


class SoftwarePreviewRequest(FrozenModel):
    """Exact plan and target binding for Preview generation."""

    plan: SoftwareUninstallAnalysisPlan
    identity_digest: str = Field(min_length=64, max_length=64)


class SoftwarePreviewResult(FrozenModel):
    """Declared zero-execution Preview tool result."""

    preview: SoftwareUninstallPreview
    execution_performed: bool = False


class SoftwareAnalysisOutcome(FrozenModel):
    """Application-service result containing candidates or one final read-only Preview."""

    resolution: ResolvedSoftwareTarget
    preview: SoftwareUninstallPreview | None = None
    execution_performed: bool = False

    @model_validator(mode="after")
    def bind_preview_to_resolution(self) -> Self:
        """Require Preview only for the exact selected resolution."""
        if self.resolution.selected is None and self.preview is not None:
            raise ValueError("An ambiguous resolution cannot have a Preview")
        if self.resolution.selected is not None and self.preview is None:
            raise ValueError("An exact resolution requires a Preview")
        if self.preview is not None and (
            self.preview.identity_digest != self.resolution.selected.identity.canonical_digest()  # type: ignore[union-attr]
        ):
            raise ValueError("Preview and resolution identities differ")
        return self
