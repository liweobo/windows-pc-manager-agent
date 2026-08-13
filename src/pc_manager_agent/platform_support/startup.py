"""Platform-neutral boundary for finite startup inspection and mutation."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pc_manager_agent.domain.startup_actions import (
    StartupBackupPayload,
    StartupIdentity,
    StartupObservation,
)


class StartupManagementPlatform(Protocol):
    """Inspect and mutate only the explicitly supported startup sources."""

    def list_entries(self, max_items: int = 5_000) -> tuple[StartupObservation, ...]:
        """Return a bounded fresh local inventory without changing startup state."""
        ...

    def inspect(self, identity: StartupIdentity) -> StartupObservation | None:
        """Re-read one exact identity, returning ``None`` when it is no longer active."""
        ...

    def capture_backup(
        self,
        identity: StartupIdentity,
        backup_id: UUID,
    ) -> StartupBackupPayload:
        """Capture exact restore material after revalidating the live identity."""
        ...

    def disable(self, payload: StartupBackupPayload) -> None:
        """Disable one exact backed-up entry without permanent deletion or overwrite."""
        ...

    def restore(self, payload: StartupBackupPayload) -> None:
        """Restore one exact backup only while the original location is conflict-free."""
        ...

    def disabled_material_matches(self, payload: StartupBackupPayload) -> bool:
        """Return whether Agent-managed disabled material still matches its backup."""
        ...
