"""Narrow operating-system contracts used by controlled Stage 4E2 cleanup."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pc_manager_agent.domain.system_cleanup_execution import RecycleBinInventorySnapshot


class CleanupActivityProbe(Protocol):
    """Check whether Windows currently grants non-forced delete access."""

    def delete_access_available(self, path: Path) -> bool:
        """Return false for access denial, sharing violations, or unavailable objects."""
        ...


class RecycleBinEmptyPlatform(Protocol):
    """Inspect and empty one exact current-user Recycle Bin volume scope."""

    def inspect(self, volume_root: Path) -> RecycleBinInventorySnapshot:
        """Return bounded current-user Shell namespace evidence for one volume."""
        ...

    def empty(self, volume_root: Path) -> int:
        """Empty one exact volume and return the Shell HRESULT without fallback."""
        ...
