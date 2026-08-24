"""Shared identity-aware Windows Recycle Bin execution primitive."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pc_manager_agent.domain.file_operations import FileObjectKind, FileState
from pc_manager_agent.domain.trash import RecycleBinResult, TrashObjectSnapshot
from pc_manager_agent.platform_support.base import FileOperationPlatform, RecycleBinPlatform
from pc_manager_agent.tools.manifest import CancellationToken


class VerifiedRecycleBinExecutor:
    """Revalidate exact identity and tree snapshot before one Shell recycle call."""

    def __init__(
        self,
        identity_platform: FileOperationPlatform,
        recycle_platform: RecycleBinPlatform,
    ) -> None:
        self._identity = identity_platform
        self._recycle = recycle_platform

    def recycle(
        self,
        source: Path,
        expected_state: FileState,
        expected_snapshot: TrashObjectSnapshot,
        snapshotter: Callable[[Path, FileObjectKind], TrashObjectSnapshot],
        cancellation: CancellationToken,
    ) -> RecycleBinResult:
        """Perform the final TOCTOU gate and invoke no API except Recycle Bin placement."""
        if cancellation.is_cancelled:
            raise RuntimeError("Recycle operation cancelled before Windows Shell call")
        current = self._identity.inspect(source)
        if not current.unchanged_since(expected_state):
            raise PermissionError("Recycle source identity or metadata changed")
        snapshot = snapshotter(source, current.kind)
        if snapshot.canonical_digest() != expected_snapshot.canonical_digest():
            raise PermissionError("Recycle source tree changed after runtime confirmation")
        if cancellation.is_cancelled:
            raise RuntimeError("Recycle operation cancelled before Windows Shell call")
        return self._recycle.recycle(source)
