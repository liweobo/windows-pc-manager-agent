from __future__ import annotations

import os
import stat
from pathlib import Path

from pc_manager_agent.domain.file_operations import FileObjectKind, FileState
from pc_manager_agent.domain.trash import (
    RecycleBinCapability,
    RecycleBinResult,
    RecycleVerificationStatus,
)


class FakeRecyclePlatform:
    """Test double that can never permanently delete an object."""

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.calls: list[Path] = []
        self.result_override: RecycleBinResult | None = None

    def capability(self, path: Path) -> RecycleBinCapability:
        if not self.available:
            return RecycleBinCapability(available=False, reason="test capability unavailable")
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
        self.calls.append(path)
        if self.result_override is not None:
            return self.result_override
        # The double deliberately leaves the source in place; production verification is
        # exercised through the result evidence, not by deleting pytest fixture data.
        return RecycleBinResult(
            source=path,
            hresult=0,
            aborted=False,
            recycled=True,
            recycle_item_identifier=f"recycle://{path.name}",
            verification_status=RecycleVerificationStatus.VERIFIED_RECYCLED,
            message="test Recycle Bin verified",
        )


class FakeTrashIdentityPlatform:
    """Identity test double compatible with real temporary files and directories."""

    def inspect(self, path: Path) -> FileState:
        canonical = Path(os.path.abspath(path))
        metadata = os.stat(canonical, follow_symlinks=False)
        kind = FileObjectKind.DIRECTORY if stat.S_ISDIR(metadata.st_mode) else FileObjectKind.FILE
        return FileState(
            path=canonical,
            kind=kind,
            volume_serial=1,
            file_id=f"{metadata.st_dev:x}-{metadata.st_ino:x}",
            size_bytes=metadata.st_size if kind is FileObjectKind.FILE else 0,
            created_ns=metadata.st_ctime_ns,
            modified_ns=metadata.st_mtime_ns,
            attributes=int(getattr(metadata, "st_file_attributes", 0)),
        )

    def move_same_volume(self, source: Path, destination: Path) -> None:
        raise AssertionError("Stage 2B must not call move_same_volume")

    def create_directory(self, destination: Path) -> None:
        raise AssertionError("Stage 2B must not call create_directory")

    def remove_empty_directory(self, path: Path) -> None:
        raise AssertionError("Stage 2B must not call remove_empty_directory")
