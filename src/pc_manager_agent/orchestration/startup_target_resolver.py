"""Deterministic local target resolution for startup entries."""

from __future__ import annotations

from pc_manager_agent.domain.startup_actions import (
    StartupErrorCode,
    StartupIdentity,
    StartupObservation,
)
from pc_manager_agent.domain.startup_errors import StartupActionError
from pc_manager_agent.platform_support.startup import StartupManagementPlatform


class StartupTargetResolver:
    """Resolve current startup objects without accepting model-authored registry locations."""

    def __init__(self, platform: StartupManagementPlatform, *, max_items: int = 5_000) -> None:
        if max_items < 1:
            raise ValueError("Startup target inventory limit must be positive")
        self._platform = platform
        self._max_items = max_items

    def list_current(self) -> tuple[StartupObservation, ...]:
        """Return the fresh bounded startup inventory."""
        return self._platform.list_entries(self._max_items)

    def resolve_name(self, name: str) -> StartupObservation:
        """Resolve one exact case-insensitive display name or fail on ambiguity."""
        normalized = name.strip().casefold()
        if not normalized:
            raise ValueError("Choose one startup entry")
        matches = tuple(
            item for item in self.list_current() if item.display_name.casefold() == normalized
        )
        if not matches:
            raise StartupActionError(
                StartupErrorCode.TARGET_NOT_FOUND,
                "No current startup entry has that exact name",
            )
        if len(matches) != 1:
            raise StartupActionError(
                StartupErrorCode.TARGET_AMBIGUOUS,
                "Multiple startup sources use that name; select one visible row",
            )
        return matches[0]

    def resolve_identity(self, identity: StartupIdentity) -> StartupObservation:
        """Re-read one locally selected stable identity and fail if absent/replaced."""
        observation = self._platform.inspect(identity)
        if observation is None:
            raise StartupActionError(
                StartupErrorCode.TARGET_NOT_FOUND,
                "The selected startup entry is no longer active",
            )
        return observation
