"""Registered R2 tool that exposes only verified Windows Recycle Bin placement."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from pc_manager_agent.domain.file_operations import FileObjectKind, FileState
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.trash import RecycleBinResult, TrashObjectSnapshot
from pc_manager_agent.platform_support.base import FileOperationPlatform, RecycleBinPlatform
from pc_manager_agent.safety.trash_policy import TrashPathPolicy
from pc_manager_agent.tools.file_tools.recycle_executor import VerifiedRecycleBinExecutor
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class TrashRequest(BaseModel):
    """Exact path and expected identity reserved by a confirmed R2 transaction item."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Path
    expected_source_state: FileState
    expected_snapshot: TrashObjectSnapshot


class TrashResult(BaseModel):
    """Validated wrapper around the platform's per-object Recycle Bin evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: RecycleBinResult


class TrashTool:
    """Revalidate identity and invoke the Recycle Bin adapter once without fallback."""

    def __init__(
        self,
        policy: TrashPathPolicy,
        identity_platform: FileOperationPlatform,
        recycle_platform: RecycleBinPlatform,
        snapshotter: Callable[[Path, FileObjectKind], TrashObjectSnapshot],
    ) -> None:
        self._policy = policy
        self._snapshotter = snapshotter
        self._executor = VerifiedRecycleBinExecutor(identity_platform, recycle_platform)

    @property
    def manifest(self) -> ToolManifest:
        """Declare R2, Preview, double-confirmation, and MANUAL recovery requirements."""
        return ToolManifest(
            name="file.trash",
            description="Move one explicitly selected object to Windows Recycle Bin",
            input_model=TrashRequest,
            output_model=TrashResult,
            risk_level=RiskLevel.R2,
            required_permissions=("ordinary-user", "authorized-source"),
            read_only=False,
            idempotent=False,
            supports_cancellation=True,
            rollback_level=RollbackLevel.MANUAL,
            preconditions=(
                "R2 plan and runtime confirmations are current",
                "source identity and directory snapshot are unchanged",
                "Recycle Bin capability was proved",
            ),
            postconditions=("Shell callback proves the object is in Recycle Bin",),
            timeout_seconds=120.0,
            max_batch_size=1,
            audit_fields=("source", "expected_source_state"),
            supported_platforms=("windows",),
            requires_confirmation=True,
            requires_runtime_confirmation=True,
            supports_preview=True,
            scope_argument_names=("source",),
        )

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Stop before the atomic Shell call, then require exact source identity."""
        typed = TrashRequest.model_validate(request)
        source = self._policy.validate_source(typed.source)
        outcome = self._executor.recycle(
            source,
            typed.expected_source_state,
            typed.expected_snapshot,
            self._snapshotter,
            cancellation,
        )
        return TrashResult(outcome=outcome)
