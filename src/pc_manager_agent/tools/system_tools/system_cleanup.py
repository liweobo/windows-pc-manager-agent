"""Isolated Stage 4E2 Fresh-prepare, Recycle Bin item, and empty tools."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

from pc_manager_agent.domain.file_operations import FileObjectKind
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.system_cleanup_execution import (
    CleanupVerificationStatus,
    RecycleBinEmptyRequest,
    RecycleBinEmptyResult,
    RecycleBinInventoryRequest,
    RecycleBinInventorySnapshot,
    SystemCleanupAssessment,
    SystemCleanupRequest,
    SystemCleanupTrashRequest,
    SystemCleanupTrashResult,
)
from pc_manager_agent.domain.trash import TrashObjectSnapshot
from pc_manager_agent.persistence.system_cleanup import SystemCleanupRepository
from pc_manager_agent.platform_support.base import FileOperationPlatform, RecycleBinPlatform
from pc_manager_agent.platform_support.system_cleanup import RecycleBinEmptyPlatform
from pc_manager_agent.safety.system_cleanup_revalidation import FreshCleanupCandidateRevalidator
from pc_manager_agent.tools.file_tools.recycle_executor import VerifiedRecycleBinExecutor
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class OptimizationCleanupPrepareTool:
    """Freshly discover exact children from session-local Stage 4E1 intent."""

    def __init__(self, revalidator: FreshCleanupCandidateRevalidator) -> None:
        self._revalidator = revalidator

    @property
    def manifest(self) -> ToolManifest:
        """Declare bounded metadata-only discovery with no execution authority."""
        return ToolManifest(
            name="optimization.cleanup.prepare",
            description="Freshly discover and classify exact selected cleanup candidates",
            input_model=SystemCleanupRequest,
            output_model=SystemCleanupAssessment,
            risk_level=RiskLevel.R0,
            required_permissions=("ordinary-user", "session-local-stage4e1-report"),
            read_only=True,
            idempotent=False,
            supports_cancellation=True,
            rollback_level=RollbackLevel.NONE,
            preconditions=("source report is current and candidate IDs are explicit",),
            postconditions=("every discovered object has a fresh eligibility decision",),
            timeout_seconds=120.0,
            max_batch_size=20,
            audit_fields=("request_id", "source_report_id", "selected_candidate_ids"),
            supported_platforms=("windows",),
            requires_confirmation=False,
        )

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Return Fresh evidence; the input cannot contain a path or force flag."""
        return self._revalidator.assess(SystemCleanupRequest.model_validate(request), cancellation)


class OptimizationCleanupTrashTool:
    """Resolve one durable reference and invoke only the shared Recycle Bin primitive."""

    def __init__(
        self,
        repository: SystemCleanupRepository,
        revalidator: FreshCleanupCandidateRevalidator,
        identity_platform: FileOperationPlatform,
        recycle_platform: RecycleBinPlatform,
    ) -> None:
        self._repository = repository
        self._revalidator = revalidator
        self._executor = VerifiedRecycleBinExecutor(identity_platform, recycle_platform)

    @property
    def manifest(self) -> ToolManifest:
        """Advertise maximum batch risk and truthful MANUAL recovery."""
        return ToolManifest(
            name="optimization.cleanup.trash",
            description="Move one durably validated cleanup object to Windows Recycle Bin",
            input_model=SystemCleanupTrashRequest,
            output_model=SystemCleanupTrashResult,
            risk_level=RiskLevel.R2_HIGH_IMPACT,
            required_permissions=("ordinary-user", "fresh-cleanup-capability"),
            read_only=False,
            idempotent=False,
            supports_cancellation=True,
            rollback_level=RollbackLevel.MANUAL,
            preconditions=(
                "Fresh all-eligible Preview is current",
                "plan and immediate confirmations were atomically consumed",
                "identity, material, protection, activity and recoverability are unchanged",
            ),
            postconditions=("Shell outcome is returned for independent identity verification",),
            timeout_seconds=120.0,
            max_batch_size=1,
            audit_fields=(
                "transaction_id",
                "cleanup_plan_id",
                "preview_id",
                "validated_item_ref",
            ),
            supported_platforms=("windows",),
            requires_confirmation=True,
            requires_runtime_confirmation=True,
            supports_preview=True,
            allowed_risk_levels=(RiskLevel.R2, RiskLevel.R2_HIGH_IMPACT),
        )

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Repeat final TOCTOU checks and provide no permanent-delete fallback."""
        typed = SystemCleanupTrashRequest.model_validate(request)
        item = self._repository.load_item(typed.transaction_id, typed.validated_item_ref)
        candidate = item.candidate
        if (
            candidate.item_ref != typed.validated_item_ref
            or candidate.path is None
            or candidate.fresh_identity is None
            or candidate.material is None
        ):
            raise PermissionError("Cleanup item lacks exact Fresh execution evidence")

        def snapshotter(path: Path, _kind: FileObjectKind) -> TrashObjectSnapshot:
            current = self._revalidator.require_unchanged(candidate, cancellation)
            if current.path != path or current.material is None:
                raise PermissionError("Cleanup object changed during final validation")
            return current.material.tree

        outcome = self._executor.recycle(
            candidate.path,
            candidate.fresh_identity.state,
            candidate.material.tree,
            snapshotter,
            cancellation,
        )
        return SystemCleanupTrashResult(
            item_ref=candidate.item_ref,
            original_identity=candidate.fresh_identity.state,
            outcome=outcome,
        )


class OptimizationRecycleBinInspectTool:
    """Read exact current-user system-volume Recycle Bin evidence."""

    def __init__(self, platform: RecycleBinEmptyPlatform) -> None:
        self._platform = platform

    @property
    def manifest(self) -> ToolManifest:
        """Declare independent read-only Recycle Bin inspection."""
        return ToolManifest(
            name="optimization.recycle_bin.inspect",
            description="Inspect the current user's Recycle Bin on the system volume",
            input_model=RecycleBinInventoryRequest,
            output_model=RecycleBinInventorySnapshot,
            risk_level=RiskLevel.R0,
            required_permissions=("ordinary-user",),
            read_only=True,
            idempotent=False,
            supports_cancellation=False,
            rollback_level=RollbackLevel.NONE,
            preconditions=("exact system volume is available",),
            postconditions=("aggregate and Shell namespace evidence are reported",),
            timeout_seconds=30.0,
            max_batch_size=1,
            audit_fields=("scope",),
            supported_platforms=("windows",),
            requires_confirmation=False,
        )

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Ignore no caller paths because the request contains only a fixed scope enum."""
        RecycleBinInventoryRequest.model_validate(request)
        if cancellation.is_cancelled:
            raise RuntimeError("Recycle Bin inspection cancelled")
        return self._platform.inspect(_system_volume())


class OptimizationRecycleBinEmptyTool:
    """Empty one prevalidated exact volume without a delete or shell fallback."""

    def __init__(
        self,
        repository: SystemCleanupRepository,
        platform: RecycleBinEmptyPlatform,
    ) -> None:
        self._repository = repository
        self._platform = platform

    @property
    def manifest(self) -> ToolManifest:
        """Declare a separate R2_HIGH_IMPACT action with recovery NONE."""
        return ToolManifest(
            name="optimization.recycle_bin.empty",
            description="Empty the current user's Recycle Bin on the exact system volume",
            input_model=RecycleBinEmptyRequest,
            output_model=RecycleBinEmptyResult,
            risk_level=RiskLevel.R2_HIGH_IMPACT,
            required_permissions=("ordinary-user", "fresh-recycle-bin-empty-capability"),
            read_only=False,
            idempotent=False,
            supports_cancellation=True,
            rollback_level=RollbackLevel.NONE,
            preconditions=(
                "independent complete Recycle Bin Preview is current",
                "separate plan and immediate confirmations were atomically consumed",
                "exact volume aggregate and namespace inventory are unchanged",
            ),
            postconditions=("fresh exact-volume inventory verifies empty or reports uncertainty",),
            timeout_seconds=120.0,
            max_batch_size=1,
            audit_fields=("transaction_id", "plan_id", "preview_id"),
            supported_platforms=("windows",),
            requires_confirmation=True,
            requires_runtime_confirmation=True,
            supports_preview=True,
            irreversible=True,
            allowed_risk_levels=(RiskLevel.R2_HIGH_IMPACT,),
        )

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Revalidate once, call SHEmptyRecycleBinW once, then independently inspect."""
        typed = RecycleBinEmptyRequest.model_validate(request)
        plan = self._repository.load_empty_plan(typed.transaction_id)
        if plan.plan_id != typed.plan_id:
            raise PermissionError("Recycle Bin empty plan reference changed")
        if cancellation.is_cancelled:
            raise RuntimeError("Recycle Bin emptying cancelled before final validation")
        before = self._platform.inspect(plan.snapshot.volume_root)
        if not before.enumeration_complete or before.canonical_digest() != plan.snapshot_digest:
            raise PermissionError("Recycle Bin contents changed after confirmation")
        if cancellation.is_cancelled:
            raise RuntimeError("Recycle Bin emptying cancelled before Shell call")
        hresult = self._platform.empty(plan.snapshot.volume_root)
        after = self._platform.inspect(plan.snapshot.volume_root)
        verified = (
            hresult >= 0
            and after.enumeration_complete
            and after.item_count == 0
            and after.observed_size_bytes == 0
        )
        return RecycleBinEmptyResult(
            transaction_id=plan.transaction_id,
            volume_root=plan.snapshot.volume_root,
            hresult=hresult,
            verification_status=(
                CleanupVerificationStatus.RECYCLE_BIN_EMPTY_VERIFIED
                if verified
                else CleanupVerificationStatus.UNKNOWN
            ),
            before=before,
            after=after,
            message=(
                "Exact-volume Recycle Bin is verified empty; Agent recovery is unavailable."
                if verified
                else "Recycle Bin emptying could not be independently verified."
            ),
        )


def _system_volume() -> Path:
    """Return one explicit drive root; never return a null or empty Shell scope."""
    drive = Path(os.environ.get("SYSTEMROOT", "C:/Windows")).drive
    if not drive:
        raise OSError("Windows system volume is unavailable")
    return Path(f"{drive}\\")
