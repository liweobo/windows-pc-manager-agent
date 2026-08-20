"""Exact read-only tool allow-list for Stage 4D1 software analysis."""

from __future__ import annotations

from pydantic import BaseModel

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.software_errors import (
    SoftwareAnalysisError,
    SoftwareAnalysisErrorCode,
)
from pc_manager_agent.domain.software_uninstall_analysis import (
    SoftwareCapabilityRequest,
    SoftwareCapabilityResult,
    SoftwareInspectRequest,
    SoftwareInspectResult,
    SoftwareInventoryRequest,
    SoftwareInventoryResult,
    SoftwarePreviewRequest,
    SoftwarePreviewResult,
    SoftwareResolveRequest,
    SoftwareResolveResult,
)
from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
from pc_manager_agent.orchestration.software_inventory import SoftwareInventoryService
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.safety.software_uninstall_preview import SoftwareUninstallPreviewEngine
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


def _manifest(
    name: str,
    description: str,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
) -> ToolManifest:
    """Build one strictly read-only R0 manifest with no runtime execution gate."""
    return ToolManifest(
        name=name,
        description=description,
        input_model=input_model,
        output_model=output_model,
        risk_level=RiskLevel.R0,
        required_permissions=("current-user-query",),
        read_only=True,
        idempotent=False,
        supports_cancellation=True,
        rollback_level=RollbackLevel.NONE,
        preconditions=("R0 plan is confirmed", "audit store is available"),
        postconditions=(
            "no installer, uninstaller, shell, package removal, process, service, or file "
            "mutation occurs",
        ),
        timeout_seconds=30.0,
        max_batch_size=20_000,
        audit_fields=("identity digest", "capability digest", "counts", "zero execution"),
        supported_platforms=("windows",),
        requires_confirmation=True,
        requires_runtime_confirmation=False,
        supports_preview=False,
        irreversible=False,
    )


class SoftwareInventoryTool:
    """Collect and normalize bounded installed-software metadata."""

    def __init__(self, inventory: SoftwareInventoryService) -> None:
        self._inventory = inventory
        self._manifest = _manifest(
            "software.inventory",
            "Refresh installed-software metadata without invoking uninstall information",
            SoftwareInventoryRequest,
            SoftwareInventoryResult,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Return only the safe inventory projection; ephemeral raw commands stay internal."""
        if not isinstance(request, SoftwareInventoryRequest):
            raise TypeError("SoftwareInventoryTool received an unexpected input model")
        snapshot = self._inventory.collect(request.max_items, cancellation)
        return SoftwareInventoryResult(inventory=snapshot.inventory)


class SoftwareResolveTool:
    """Resolve one exact target or return a bounded ambiguous candidate set."""

    def __init__(self, resolver: SoftwareTargetResolver) -> None:
        self._resolver = resolver
        self._manifest = _manifest(
            "software.resolve",
            "Resolve one current software identity without automatic fuzzy selection",
            SoftwareResolveRequest,
            SoftwareResolveResult,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Refresh inventory and resolve the validated query."""
        if not isinstance(request, SoftwareResolveRequest):
            raise TypeError("SoftwareResolveTool received an unexpected input model")
        resolved, snapshot = self._resolver.resolve(request.query, request.max_items, cancellation)
        return SoftwareResolveResult(
            resolved=resolved,
            inventory_collected_at=snapshot.inventory.collected_at,
        )


class SoftwareInspectTool:
    """Re-read one exact identity for stale-state detection."""

    def __init__(self, resolver: SoftwareTargetResolver) -> None:
        self._resolver = resolver
        self._manifest = _manifest(
            "software.inspect",
            "Refresh and inspect one exact source-qualified software identity",
            SoftwareInspectRequest,
            SoftwareInspectResult,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Return a fresh exact observation or explicit disappearance."""
        if not isinstance(request, SoftwareInspectRequest):
            raise TypeError("SoftwareInspectTool received an unexpected input model")
        software, _snapshot = self._resolver.inspect(
            request.identity_digest, request.max_items, cancellation
        )
        return SoftwareInspectResult(software=software, found=software is not None)


class SoftwareUninstallCapabilityTool:
    """Analyze exact mechanism metadata without retaining or invoking commands."""

    def __init__(
        self,
        resolver: SoftwareTargetResolver,
        capability: UninstallCapabilityResolver,
    ) -> None:
        self._resolver = resolver
        self._capability = capability
        self._manifest = _manifest(
            "software.uninstall_capability",
            "Classify uninstall metadata without executing an installer or command",
            SoftwareCapabilityRequest,
            SoftwareCapabilityResult,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Refresh exact target and analyze its ephemeral raw metadata locally."""
        if not isinstance(request, SoftwareCapabilityRequest):
            raise TypeError("SoftwareUninstallCapabilityTool received an unexpected input model")
        software, snapshot = self._resolver.inspect(
            request.identity_digest, request.max_items, cancellation
        )
        if software is None:
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.TARGET_CHANGED,
                "Software target disappeared or changed before capability analysis",
            )
        raw = snapshot.raw_by_identity.get(request.identity_digest)
        if raw is None:
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.TARGET_CHANGED,
                "Raw source evidence no longer matches the software identity",
            )
        return SoftwareCapabilityResult(
            identity_digest=request.identity_digest,
            capability=self._capability.resolve(software, raw),
        )


class SoftwareUninstallPreviewTool:
    """Generate a final non-executable Preview after fresh exact revalidation."""

    def __init__(
        self,
        resolver: SoftwareTargetResolver,
        preview_engine: SoftwareUninstallPreviewEngine,
    ) -> None:
        self._resolver = resolver
        self._preview_engine = preview_engine
        self._manifest = _manifest(
            "software.uninstall_preview",
            "Prepare an expiring zero-execution uninstall impact Preview",
            SoftwarePreviewRequest,
            SoftwarePreviewResult,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the immutable R0 manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Re-read target and build a Preview that is structurally non-executable."""
        if not isinstance(request, SoftwarePreviewRequest):
            raise TypeError("SoftwareUninstallPreviewTool received an unexpected input model")
        software, snapshot = self._resolver.inspect(
            request.identity_digest, request.plan.max_items, cancellation
        )
        if software is None:
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.TARGET_CHANGED,
                "Software target disappeared or changed before Preview generation",
            )
        raw = snapshot.raw_by_identity.get(request.identity_digest)
        if raw is None:
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.TARGET_CHANGED,
                "Raw source evidence no longer matches the software identity",
            )
        preview = self._preview_engine.build(request.plan, software, raw, cancellation)
        return SoftwarePreviewResult(preview=preview)
