"""Cross-platform contracts used by the application shell."""

from typing import Protocol


class SingleInstanceGuard(Protocol):
    """Prevent concurrent instances from owning the same local state."""

    def acquire(self) -> bool:
        """Return true only when this process owns the guard."""
        ...

    def close(self) -> None:
        """Release the guard."""
        ...
