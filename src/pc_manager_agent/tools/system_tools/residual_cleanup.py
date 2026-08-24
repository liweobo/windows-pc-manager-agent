"""Registered Stage 4D4 prepare and reference-only Recycle Bin tools."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from pc_manager_agent.domain.file_operations import FileObjectKind
from pc_manager_agent.domain.residual_cleanup import (
    ResidualCleanupAssessment,
    ResidualCleanupRequest,
    ResidualCleanupTrashRequest,
    ResidualCleanupTrashResult,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.trash import TrashObjectSnapshot
from pc_manager_agent.persistence.residual_cleanup import ResidualCleanupRepository
from pc_manager_agent.platform_support.base import FileOperationPlatform, RecycleBinPlatform
from pc_manager_agent.safety.residual_cleanup_revalidation import FreshResidualRevalidator
from pc_manager_agent.tools.file_tools.recycle_executor import VerifiedRecycleBinExecutor
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class SoftwareResidualPrepareCleanupTool:
    """Run only a fresh R0 assessment from report and candidate references."""

    def __init__(self, revalidator: FreshResidualRevalidator) -> None:
        self._revalidator = revalidator

    @property
    def manifest(self) -> ToolManifest:
        """Declare metadata-only preparation with no mutation authority."""
        return ToolManifest(
            name="software.residuals.prepare_cleanup",
            description="Freshly reassess exact selected residual IDs for controlled cleanup",
            input_model=ResidualCleanupRequest,
            output_model=ResidualCleanupAssessment,
            risk_level=RiskLevel.R0,
            required_permissions=("ordinary-user", "local-residual-report"),
            read_only=True,
            idempotent=False,
            supports_cancellation=True,
            rollback_level=RollbackLevel.NONE,
            preconditions=("source report and uninstall context exist",),
            postconditions=("every selected candidate has a fresh eligibility decision",),
            timeout_seconds=120.0,
            max_batch_size=100,
            audit_fields=("request_id", "source_report_id", "selected_residual_ids"),
            supported_platforms=("windows",),
            requires_confirmation=False,
        )

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Return fresh evidence without accepting or returning execution paths as authority."""
        typed = ResidualCleanupRequest.model_validate(request)
        return self._revalidator.assess(typed, cancellation)


class SoftwareResidualTrashTool:
    """Resolve one durable item reference and delegate to the shared recycle primitive."""

    def __init__(
        self,
        repository: ResidualCleanupRepository,
        revalidator: FreshResidualRevalidator,
        identity_platform: FileOperationPlatform,
        recycle_platform: RecycleBinPlatform,
    ) -> None:
        self._repository = repository
        self._revalidator = revalidator
        self._executor = VerifiedRecycleBinExecutor(identity_platform, recycle_platform)

    @property
    def manifest(self) -> ToolManifest:
        """Declare the highest supported batch risk and mandatory manual recovery."""
        return ToolManifest(
            name="software.residuals.trash",
            description="Move one durably validated software residual to Windows Recycle Bin",
            input_model=ResidualCleanupTrashRequest,
            output_model=ResidualCleanupTrashResult,
            # Exact transaction risk may be R2 or R2_HIGH_IMPACT. The manifest
            # advertises the maximum so registration can never understate it.
            risk_level=RiskLevel.R2_HIGH_IMPACT,
            required_permissions=("ordinary-user", "fresh-residual-capability"),
            read_only=False,
            idempotent=False,
            supports_cancellation=True,
            rollback_level=RollbackLevel.MANUAL,
            preconditions=(
                "fresh all-eligible Preview is current",
                "plan and immediate confirmations are consumed",
                "exact identity and material tree are unchanged",
                "Windows Recycle Bin capability remains available",
            ),
            postconditions=(
                "Windows Shell result is returned for independent identity verification",
            ),
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
        """Resolve the path internally, repeat all gates, and invoke Recycle Bin once."""
        typed = ResidualCleanupTrashRequest.model_validate(request)
        item = self._repository.load_item(
            typed.transaction_id,
            typed.validated_item_ref,
        )
        if item.item_ref != typed.validated_item_ref:
            raise PermissionError("Residual cleanup item reference changed")
        candidate = item.candidate
        if candidate.fresh_identity is None or candidate.material is None:
            raise PermissionError("Residual cleanup item lacks fresh execution evidence")

        def snapshotter(path: Path, _kind: FileObjectKind) -> TrashObjectSnapshot:
            current = self._revalidator.require_unchanged(candidate, cancellation)
            if current.path != path or current.material is None:
                raise PermissionError("Residual cleanup path changed during final validation")
            return current.material.tree

        outcome = self._executor.recycle(
            candidate.path,
            candidate.fresh_identity,
            candidate.material.tree,
            snapshotter,
            cancellation,
        )
        return ResidualCleanupTrashResult(
            item_ref=item.item_ref,
            original_identity=candidate.fresh_identity,
            outcome=outcome,
        )
