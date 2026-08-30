"""Synthetic Stage 4E2 graph with no access to the real Windows Recycle Bin."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from tests.fixtures.system_optimization import build_system_snapshot
from tests.stage2b_support import FakeTrashIdentityPlatform

from pc_manager_agent import __version__
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.audit.system_cleanup import SystemCleanupAuditLogger
from pc_manager_agent.confirmation.system_cleanup import SystemCleanupConfirmationService
from pc_manager_agent.domain.risk import RollbackLevel
from pc_manager_agent.domain.system_cleanup_execution import RecycleBinInventorySnapshot
from pc_manager_agent.domain.system_optimization import (
    CleanupCandidate,
    CleanupCategory,
    CleanupEvidenceOrigin,
    CleanupReasonCode,
    CleanupSafetyClassification,
    CleanupSourceReference,
    OptimizationConfidence,
    OptimizationEvidence,
    OptimizationSnapshot,
    OwnershipConfidence,
    ProtectionLevel,
    SystemOptimizationReport,
)
from pc_manager_agent.domain.trash import (
    RecycleBinCapability,
    RecycleBinResult,
    RecycleVerificationStatus,
)
from pc_manager_agent.orchestration.optimization_report_store import (
    OptimizationReportSessionStore,
)
from pc_manager_agent.orchestration.system_cleanup import SystemCleanupService
from pc_manager_agent.persistence.system_cleanup import (
    SystemCleanupExecutionGuard,
    SystemCleanupRepository,
)
from pc_manager_agent.safety.recycle_bin_empty import RecycleBinEmptyPlanBuilder
from pc_manager_agent.safety.system_cleanup_policy import (
    CleanupRecentActivityPolicy,
    SystemCleanupEligibilityPolicy,
    SystemCleanupPathPolicy,
    SystemCleanupRiskPolicy,
)
from pc_manager_agent.safety.system_cleanup_preview import CleanupExecutionPlanBuilder
from pc_manager_agent.safety.system_cleanup_revalidation import (
    FreshCleanupCandidateRevalidator,
)
from pc_manager_agent.safety.system_cleanup_validator import SystemCleanupSafetyValidator
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.system_cleanup import (
    OptimizationCleanupPrepareTool,
    OptimizationCleanupTrashTool,
    OptimizationRecycleBinEmptyTool,
    OptimizationRecycleBinInspectTool,
)


class SyntheticRecycleBinPlatform:
    """Move selected fixtures to quarantine; permanent deletion is impossible."""

    def __init__(self, quarantine: Path, *, available: bool = True) -> None:
        self._quarantine = quarantine
        self.available = available
        self.calls: list[Path] = []

    def capability(self, path: Path) -> RecycleBinCapability:
        """Return deterministic local fixed-volume evidence."""
        if not self.available:
            return RecycleBinCapability(available=False, reason="synthetic unavailable")
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
        """Move one object to disposable quarantine and return Shell-like evidence."""
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


class SyntheticCleanupActivityProbe:
    """Deterministically expose ordinary delete-access availability."""

    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    def delete_access_available(self, path: Path) -> bool:
        """Return the configured result without opening or changing the object."""
        _ = path
        return self.available


class SyntheticRecycleBinEmptyPlatform:
    """Model exact-volume inventory without touching the host Recycle Bin."""

    def __init__(self, *, item_count: int = 3, observed_size_bytes: int = 4096) -> None:
        self.item_count = item_count
        self.observed_size_bytes = observed_size_bytes
        self.enumeration_complete = True
        self.empty_calls: list[Path] = []
        now = datetime.now(UTC)
        self.oldest_deleted_at = now - timedelta(days=30)
        self.newest_deleted_at = now - timedelta(days=2)

    def inspect(self, volume_root: Path) -> RecycleBinInventorySnapshot:
        """Return a stable complete snapshot until the fake empty call occurs."""
        return RecycleBinInventorySnapshot(
            volume_root=volume_root,
            item_count=self.item_count,
            observed_size_bytes=self.observed_size_bytes,
            oldest_deleted_at=self.oldest_deleted_at if self.item_count else None,
            newest_deleted_at=self.newest_deleted_at if self.item_count else None,
            enumeration_complete=self.enumeration_complete,
        )

    def empty(self, volume_root: Path) -> int:
        """Record one fake irreversible call and change only in-memory counters."""
        self.empty_calls.append(volume_root)
        self.item_count = 0
        self.observed_size_bytes = 0
        return 0


@dataclass(slots=True)
class SyntheticSystemCleanupEnvironment:
    """Complete Stage 4E2 dependency graph over disposable local state."""

    profile: Path
    temp_root: Path
    report_store: OptimizationReportSessionStore
    repository: SystemCleanupRepository
    audit: AuditRepository
    service: SystemCleanupService
    registry: ToolRegistry
    recycle: SyntheticRecycleBinPlatform
    empty_platform: SyntheticRecycleBinEmptyPlatform
    activity: SyntheticCleanupActivityProbe
    revalidator: FreshCleanupCandidateRevalidator
    plans: CleanupExecutionPlanBuilder
    empty_plans: RecycleBinEmptyPlanBuilder
    confirmations: SystemCleanupConfirmationService
    validator: SystemCleanupSafetyValidator

    def close(self) -> None:
        """Release the two isolated SQLite engines."""
        self.repository.close()
        self.audit.close()


def mark_old(path: Path, *, days: int = 30) -> None:
    """Set synthetic metadata beyond the default recent-activity cutoff."""
    timestamp = (datetime.now(UTC) - timedelta(days=days)).timestamp()
    os.utime(path, (timestamp, timestamp))


def save_temp_report(
    environment: SyntheticSystemCleanupEnvironment,
) -> SystemOptimizationReport:
    """Store one non-authoritative known-location Stage 4E1 report."""
    candidate = CleanupCandidate(
        category=CleanupCategory.USER_TEMP,
        source="current-user-temp",
        path=environment.temp_root,
        observed_size_bytes=0,
        potential_reclaim_bytes=0,
        item_count=0,
        ownership_confidence=OwnershipConfidence.HIGH,
        safety_classification=CleanupSafetyClassification.LOW_RISK_CANDIDATE,
        protection_level=ProtectionLevel.CAUTION,
        recoverability=RollbackLevel.MANUAL,
        confidence=OptimizationConfidence.MEDIUM,
        evidence=(
            OptimizationEvidence.KNOWN_LOCATION_ALLOWLIST,
            OptimizationEvidence.DIRECT_FILE_METADATA,
        ),
        reason_codes=(CleanupReasonCode.KNOWN_TEMP_LOCATION,),
        source_reference=CleanupSourceReference(origin=CleanupEvidenceOrigin.KNOWN_LOCATION),
    )
    report = SystemOptimizationReport(
        plan_id=uuid4(),
        plan_digest="1" * 64,
        snapshot=OptimizationSnapshot(system=build_system_snapshot()),
        cleanup_candidates=(candidate,),
        performance_findings=(),
        recommendations=(),
        observed_bytes=0,
        potential_reclaim_bytes=0,
        protected_bytes=0,
        unknown_bytes=0,
    )
    environment.report_store.save(report)
    return report


def build_system_cleanup_environment(
    database_path: Path,
    *,
    minimum_age_days: int = 7,
    max_selected: int = 20,
) -> SyntheticSystemCleanupEnvironment:
    """Compose production Stage 4E2 components with only non-destructive fakes."""
    profile = database_path.parent / "synthetic-user"
    temp_root = profile / "AppData" / "Local" / "Temp"
    temp_root.mkdir(parents=True)
    reports = OptimizationReportSessionStore(ttl_seconds=1800)
    repository = SystemCleanupRepository(database_path)
    repository.initialize()
    audit = AuditRepository(database_path)
    audit.initialize()
    identity = FakeTrashIdentityPlatform()
    recycle = SyntheticRecycleBinPlatform(database_path.parent / "synthetic-recycle-bin")
    empty_platform = SyntheticRecycleBinEmptyPlatform()
    activity = SyntheticCleanupActivityProbe()
    revalidator = FreshCleanupCandidateRevalidator(
        reports,
        SystemCleanupPathPolicy(
            {"current-user-temp": temp_root},
            user_profile=profile,
        ),
        SystemCleanupEligibilityPolicy(),
        CleanupRecentActivityPolicy(minimum_age_days=minimum_age_days),
        identity,
        recycle,
        activity,
        max_selected_candidates=max_selected,
        max_discovered_items=100,
        max_contained_objects=1_000,
        max_total_bytes=1024**3,
    )
    plans = CleanupExecutionPlanBuilder(
        revalidator,
        SystemCleanupRiskPolicy(
            max_normal_items=5,
            max_normal_objects=100,
            max_normal_total_bytes=1024**2,
            max_normal_single_item_bytes=512 * 1024,
        ),
        max_selected_items=max_selected,
        plan_ttl_seconds=300,
        preview_ttl_seconds=60,
    )
    empty_plans = RecycleBinEmptyPlanBuilder(
        empty_platform,
        plan_ttl_seconds=300,
        preview_ttl_seconds=60,
    )
    confirmations = SystemCleanupConfirmationService(
        repository,
        plans,
        plan_ttl_seconds=300,
        runtime_ttl_seconds=60,
    )
    registry = ToolRegistry(write_guard=SystemCleanupExecutionGuard(repository))
    for tool in (
        OptimizationCleanupPrepareTool(revalidator),
        OptimizationCleanupTrashTool(repository, revalidator, identity, recycle),
        OptimizationRecycleBinInspectTool(empty_platform),
        OptimizationRecycleBinEmptyTool(repository, empty_platform),
    ):
        registry.register(tool)
    validator = SystemCleanupSafetyValidator(registry)
    service = SystemCleanupService(
        registry,
        plans,
        empty_plans,
        validator,
        confirmations,
        repository,
        identity,
        SystemCleanupAuditLogger(audit, app_version=__version__, git_commit="test"),
    )
    return SyntheticSystemCleanupEnvironment(
        profile=profile,
        temp_root=temp_root,
        report_store=reports,
        repository=repository,
        audit=audit,
        service=service,
        registry=registry,
        recycle=recycle,
        empty_platform=empty_platform,
        activity=activity,
        revalidator=revalidator,
        plans=plans,
        empty_plans=empty_plans,
        confirmations=confirmations,
        validator=validator,
    )
