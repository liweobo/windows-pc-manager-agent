"""Synthetic Stage 4D4 environment that can never permanently delete data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tests.fixtures.software_residuals import (
    SyntheticResidualEnvironment,
    build_residual_environment,
)
from tests.stage2b_support import FakeTrashIdentityPlatform

from pc_manager_agent import __version__
from pc_manager_agent.audit.residual_cleanup import ResidualCleanupAuditLogger
from pc_manager_agent.confirmation.residual_cleanup import (
    ResidualCleanupConfirmationService,
)
from pc_manager_agent.domain.software_residuals import ResidualReport, UninstallContext
from pc_manager_agent.domain.trash import (
    RecycleBinCapability,
    RecycleBinResult,
    RecycleVerificationStatus,
)
from pc_manager_agent.orchestration.residual_cleanup import ResidualCleanupService
from pc_manager_agent.persistence.residual_cleanup import (
    ResidualCleanupExecutionGuard,
    ResidualCleanupRepository,
)
from pc_manager_agent.safety.residual_classification import ResidualClassifier
from pc_manager_agent.safety.residual_cleanup_policy import (
    CleanupEligibilityPolicy,
    CleanupRiskPolicy,
    ResidualCleanupPathPolicy,
    ResidualRecentModificationPolicy,
)
from pc_manager_agent.safety.residual_cleanup_preview import ResidualCleanupPreviewEngine
from pc_manager_agent.safety.residual_cleanup_revalidation import FreshResidualRevalidator
from pc_manager_agent.safety.residual_cleanup_validator import (
    ResidualCleanupSafetyValidator,
)
from pc_manager_agent.safety.residual_ownership import ResidualOwnershipEvaluator
from pc_manager_agent.safety.residual_scope_policy import ResidualScanScopePolicy
from pc_manager_agent.safety.user_data_protection import UserDataProtectionPolicy
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.residual_cleanup import (
    SoftwareResidualPrepareCleanupTool,
    SoftwareResidualTrashTool,
)


class MovingRecyclePlatform:
    """Test adapter that moves objects into a disposable quarantine directory."""

    def __init__(self, quarantine: Path, *, available: bool = True) -> None:
        self._quarantine = quarantine
        self.available = available
        self.calls: list[Path] = []

    def capability(self, path: Path) -> RecycleBinCapability:
        """Return deterministic fixed-volume evidence for a synthetic local path."""
        if not self.available:
            return RecycleBinCapability(
                available=False,
                reason="synthetic recycle capability unavailable",
            )
        return RecycleBinCapability(
            available=True,
            volume_root=Path(path.anchor or "C:/"),
            filesystem="NTFS",
            volume_serial=1,
            fixed_drive=True,
            read_only=False,
            hotplug=False,
            recycle_bin_query_succeeded=True,
        )

    def recycle(self, path: Path) -> RecycleBinResult:
        """Move into test quarantine; never unlink, rmdir, or permanently delete."""
        self.calls.append(path)
        self._quarantine.mkdir(parents=True, exist_ok=True)
        destination = self._quarantine / f"{len(self.calls):04d}-{path.name}"
        path.replace(destination)
        return RecycleBinResult(
            source=path,
            hresult=0,
            aborted=False,
            recycled=True,
            recycle_item_identifier=f"synthetic-recycle://{destination.name}",
            verification_status=RecycleVerificationStatus.VERIFIED_RECYCLED,
            message="synthetic Recycle Bin placement verified",
        )


@dataclass(slots=True)
class SyntheticResidualCleanupEnvironment:
    """Complete Stage 4D3 + Stage 4D4 graph over one isolated database."""

    residuals: SyntheticResidualEnvironment
    cleanup_repository: ResidualCleanupRepository
    registry: ToolRegistry
    service: ResidualCleanupService
    recycle: MovingRecyclePlatform
    revalidator: FreshResidualRevalidator
    preview: ResidualCleanupPreviewEngine
    confirmations: ResidualCleanupConfirmationService

    def close(self) -> None:
        """Release all isolated SQLite engines."""
        self.cleanup_repository.close()
        self.residuals.close()


def create_residual_report(
    environment: SyntheticResidualCleanupEnvironment,
    context: UninstallContext,
) -> ResidualReport:
    """Persist and run the required confirmed Stage 4D3 read-only analysis."""
    environment.residuals.repository.upsert_context(context)
    plan, loaded, review = environment.residuals.service.prepare(
        "analyze exact post-uninstall residual metadata",
        context.transaction_id,
    )
    if not review.approved:
        raise AssertionError("Synthetic Stage 4D3 plan was unexpectedly blocked")
    confirmation = environment.residuals.service.request_plan_confirmation(plan, loaded)
    environment.residuals.service.resolve_plan_confirmation(
        confirmation.confirmation_id,
        True,
        plan,
        loaded,
    )
    return environment.residuals.service.analyze(plan, loaded)


def build_residual_cleanup_environment(
    database_path: Path,
    *,
    recycle_available: bool = True,
    max_selected: int = 20,
    max_objects: int = 10_000,
    max_bytes: int = 50 * 1024 * 1024 * 1024,
) -> SyntheticResidualCleanupEnvironment:
    """Compose production Stage 4D4 components with a non-destructive test adapter."""
    residuals = build_residual_environment(database_path)
    cleanup_repository = ResidualCleanupRepository(database_path)
    cleanup_repository.initialize()
    identity = FakeTrashIdentityPlatform()
    recycle = MovingRecyclePlatform(
        database_path.parent / "synthetic-recycle-bin",
        available=recycle_available,
    )
    scope = ResidualScanScopePolicy(max_roots=16)
    path_policy = ResidualCleanupPathPolicy(
        scope,
        access_checker=lambda _path, _mode: True,
        user_profile=database_path.parent / "synthetic-user-profile",
    )
    revalidator = FreshResidualRevalidator(
        residuals.repository,
        path_policy,
        ResidualClassifier(),
        ResidualOwnershipEvaluator(),
        UserDataProtectionPolicy(database_path.parent / "synthetic-user-profile"),
        CleanupEligibilityPolicy(),
        ResidualRecentModificationPolicy(),
        identity,
        recycle,
        max_selected=max_selected,
        max_contained_objects=max_objects,
        max_total_bytes=max_bytes,
    )
    preview = ResidualCleanupPreviewEngine(
        revalidator,
        CleanupRiskPolicy(
            max_normal_item_count=5,
            max_normal_object_count=100,
            max_normal_total_size=1024 * 1024 * 1024,
            max_normal_single_item_size=512 * 1024 * 1024,
        ),
        plan_ttl_seconds=900,
        preview_ttl_seconds=300,
    )
    confirmations = ResidualCleanupConfirmationService(
        cleanup_repository,
        preview,
        plan_ttl_seconds=300,
        runtime_ttl_seconds=60,
    )
    registry = ToolRegistry(ResidualCleanupExecutionGuard(cleanup_repository))
    registry.register(SoftwareResidualPrepareCleanupTool(revalidator))
    registry.register(
        SoftwareResidualTrashTool(
            cleanup_repository,
            revalidator,
            identity,
            recycle,
        )
    )
    service = ResidualCleanupService(
        registry,
        preview,
        ResidualCleanupSafetyValidator(registry),
        confirmations,
        cleanup_repository,
        identity,
        ResidualCleanupAuditLogger(
            residuals.audit,
            app_version=__version__,
            git_commit="test",
        ),
    )
    return SyntheticResidualCleanupEnvironment(
        residuals=residuals,
        cleanup_repository=cleanup_repository,
        registry=registry,
        service=service,
        recycle=recycle,
        revalidator=revalidator,
        preview=preview,
        confirmations=confirmations,
    )
