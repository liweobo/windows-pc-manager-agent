"""Command-style startup mutations with verification and exact reverse action."""

from __future__ import annotations

from pc_manager_agent.domain.startup_actions import StartupBackupPayload
from pc_manager_agent.platform_support.startup import StartupManagementPlatform


class DisableStartupCommand:
    """Disable one exact entry and restore it if verification fails."""

    def __init__(
        self,
        platform: StartupManagementPlatform,
        payload: StartupBackupPayload,
    ) -> None:
        self._platform = platform
        self._payload = payload

    def execute(self) -> None:
        """Apply the narrow source-specific disable mutation."""
        self._platform.disable(self._payload)

    def verify(self) -> bool:
        """Verify the active source is absent and disabled material remains exact."""
        return self._platform.inspect(
            self._payload.original_identity
        ) is None and self._platform.disabled_material_matches(self._payload)

    def rollback(self) -> bool:
        """Restore the exact backup if the active location is still conflict-free."""
        self._platform.restore(self._payload)
        return self._platform.inspect(self._payload.original_identity) is not None


class RestoreStartupCommand:
    """Restore one Agent-disabled entry and re-disable it if verification fails."""

    def __init__(
        self,
        platform: StartupManagementPlatform,
        payload: StartupBackupPayload,
    ) -> None:
        self._platform = platform
        self._payload = payload

    def execute(self) -> None:
        """Restore exact registry or shell-link material without overwrite."""
        self._platform.restore(self._payload)

    def verify(self) -> bool:
        """Verify the restored active identity exactly matches its backup identity."""
        return self._platform.inspect(self._payload.original_identity) is not None

    def rollback(self) -> bool:
        """Return to the exact Agent-disabled state if restore verification fails."""
        self._platform.disable(self._payload)
        return self._platform.inspect(
            self._payload.original_identity
        ) is None and self._platform.disabled_material_matches(self._payload)
