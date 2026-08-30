"""Independent Fresh planning for irreversible exact-volume Recycle Bin emptying."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pc_manager_agent.domain.system_cleanup_execution import (
    RecycleBinEmptyPlan,
    RecycleBinEmptyPreview,
)
from pc_manager_agent.platform_support.system_cleanup import RecycleBinEmptyPlatform


class RecycleBinEmptyPreviewError(PermissionError):
    """Raised when exact-volume inventory is empty, partial, stale, or unavailable."""


class RecycleBinEmptyPlanBuilder:
    """Keep Bin emptying outside ordinary cleanup batches and recovery claims."""

    def __init__(
        self,
        platform: RecycleBinEmptyPlatform,
        *,
        plan_ttl_seconds: int,
        preview_ttl_seconds: int,
    ) -> None:
        if min(plan_ttl_seconds, preview_ttl_seconds) <= 0:
            raise ValueError("Recycle Bin empty Preview TTLs must be positive")
        self._platform = platform
        self._plan_ttl = plan_ttl_seconds
        self._preview_ttl = preview_ttl_seconds

    def prepare(self) -> tuple[RecycleBinEmptyPlan, RecycleBinEmptyPreview]:
        """Inspect the fixed scope and create a separate R2_HIGH_IMPACT plan."""
        snapshot = self._platform.inspect(self._system_volume())
        if not snapshot.enumeration_complete:
            raise RecycleBinEmptyPreviewError("Recycle Bin inventory is incomplete")
        if snapshot.item_count == 0:
            raise RecycleBinEmptyPreviewError("Recycle Bin is already empty")
        now = datetime.now(UTC)
        plan = RecycleBinEmptyPlan(
            snapshot=snapshot,
            snapshot_digest=snapshot.canonical_digest(),
            created_at=now,
            expires_at=now + timedelta(seconds=self._plan_ttl),
        )
        return plan, self._preview(plan, snapshot)

    def revalidate(self, plan: RecycleBinEmptyPlan) -> RecycleBinEmptyPreview:
        """Require the exact same aggregate, size, age bounds, and volume before approval."""
        if datetime.now(UTC) >= plan.expires_at:
            raise RecycleBinEmptyPreviewError("Recycle Bin empty plan expired")
        snapshot = self._platform.inspect(plan.snapshot.volume_root)
        if not snapshot.enumeration_complete or snapshot.canonical_digest() != plan.snapshot_digest:
            raise RecycleBinEmptyPreviewError(
                "Recycle Bin contents changed; inspect and confirm a new plan"
            )
        return self._preview(plan, snapshot)

    def _preview(self, plan: RecycleBinEmptyPlan, snapshot: object) -> RecycleBinEmptyPreview:
        from pc_manager_agent.domain.system_cleanup_execution import RecycleBinInventorySnapshot

        typed = RecycleBinInventorySnapshot.model_validate(snapshot)
        now = datetime.now(UTC)
        return RecycleBinEmptyPreview(
            transaction_id=plan.transaction_id,
            plan_id=plan.plan_id,
            plan_digest=plan.canonical_digest(),
            snapshot=typed,
            generated_at=now,
            expires_at=min(plan.expires_at, now + timedelta(seconds=self._preview_ttl)),
        )

    @staticmethod
    def _system_volume() -> Path:
        drive = Path(os.environ.get("SYSTEMROOT", "C:/Windows")).drive
        if not drive:
            raise OSError("Windows system volume is unavailable")
        return Path(f"{drive}\\")
