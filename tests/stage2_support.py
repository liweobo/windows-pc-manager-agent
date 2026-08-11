from __future__ import annotations

import os
import stat
from pathlib import Path

from pc_manager_agent.domain.file_operations import FileObjectKind, FileState


class FakeFileOperationPlatform:
    """Test double with deterministic identity, volume, and injectable failures."""

    def __init__(self) -> None:
        self.volume_overrides: dict[Path, int] = {}
        self.inspect_errors: dict[Path, OSError] = {}
        self.move_error: OSError | None = None
        self.create_error: OSError | None = None
        self.remove_error: OSError | None = None
        self.move_calls: list[tuple[Path, Path]] = []

    def inspect(self, path: Path) -> FileState:
        canonical = Path(os.path.abspath(path))
        error = self.inspect_errors.get(canonical)
        if error is not None:
            raise error
        metadata = os.stat(canonical, follow_symlinks=False)
        kind = FileObjectKind.DIRECTORY if stat.S_ISDIR(metadata.st_mode) else FileObjectKind.FILE
        volume = next(
            (
                value
                for root, value in self.volume_overrides.items()
                if canonical == root or root in canonical.parents
            ),
            1,
        )
        return FileState(
            path=canonical,
            kind=kind,
            volume_serial=volume,
            file_id=f"{metadata.st_dev:x}-{metadata.st_ino:x}",
            size_bytes=metadata.st_size if kind is FileObjectKind.FILE else 0,
            created_ns=metadata.st_ctime_ns,
            modified_ns=metadata.st_mtime_ns,
            attributes=0,
        )

    def move_same_volume(self, source: Path, destination: Path) -> None:
        self.move_calls.append((source, destination))
        if self.move_error is not None:
            raise self.move_error
        source.rename(destination)

    def create_directory(self, destination: Path) -> None:
        if self.create_error is not None:
            raise self.create_error
        destination.mkdir()

    def remove_empty_directory(self, path: Path) -> None:
        if self.remove_error is not None:
            raise self.remove_error
        path.rmdir()
