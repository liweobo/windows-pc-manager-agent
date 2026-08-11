"""Cross-platform contracts used by deterministic application services."""

from pathlib import Path
from typing import Protocol

from pc_manager_agent.domain.file_operations import FileState


class SingleInstanceGuard(Protocol):
    """Prevent concurrent instances from owning the same local state."""

    def acquire(self) -> bool:
        """Return true only when this process owns the guard."""
        ...

    def close(self) -> None:
        """Release the guard."""
        ...


class FileOperationPlatform(Protocol):
    """OS adapter for identity-aware, no-overwrite filesystem mutations."""

    def inspect(self, path: Path) -> FileState:
        """Open an existing object without following reparse points and return identity."""
        ...

    def move_same_volume(self, source: Path, destination: Path) -> None:
        """Move one object without copying, deleting, or replacing a destination."""
        ...

    def create_directory(self, destination: Path) -> None:
        """Create exactly one directory and fail if the target already exists."""
        ...

    def remove_empty_directory(self, path: Path) -> None:
        """Remove exactly one empty, already revalidated directory during rollback."""
        ...
