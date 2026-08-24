"""Conservative protection policy independent from residual ownership."""

from __future__ import annotations

import os
from pathlib import Path

from pc_manager_agent.domain.software_residuals import (
    ResidualClassification,
    UserDataProtectionLevel,
)
from pc_manager_agent.safety.path_policy import path_is_within

_STRONGLY_PROTECTED_CLASSES = frozenset(
    {
        ResidualClassification.USER_DATA,
        ResidualClassification.DATABASE,
        ResidualClassification.PACKAGE_USER_DATA,
    }
)
_PROTECTED_CLASSES = frozenset(
    {
        ResidualClassification.CONFIGURATION,
        ResidualClassification.PLUGIN_OR_EXTENSION,
        ResidualClassification.LICENSE_DATA,
        ResidualClassification.APPLICATION_STATE,
        ResidualClassification.UNKNOWN,
    }
)
_DEVELOPMENT_COMPONENTS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "workspace",
        "workspaces",
        "projects",
        "repos",
    }
)
_HIGH_VALUE_COMPONENTS = frozenset(
    {
        "docker",
        "wsl",
        "outlook",
        "thunderbird",
        "chrome",
        "firefox",
        "edge",
        "office",
        "mail",
        "profiles",
    }
)


class UserDataProtectionPolicy:
    """Apply the highest relevant protection and never recommend deletion."""

    def __init__(self, user_profile: Path | None = None) -> None:
        profile = user_profile or Path(os.environ.get("USERPROFILE", Path.home()))
        self._strong_roots = tuple(
            profile / name
            for name in (
                "Desktop",
                "Documents",
                "Downloads",
                "Pictures",
                "Music",
                "Videos",
                "Saved Games",
            )
        )
        self._roaming = profile / "AppData" / "Roaming"

    def protect(
        self,
        path: Path,
        classification: ResidualClassification,
    ) -> tuple[UserDataProtectionLevel, tuple[str, ...]]:
        """Return a protection level and non-content reason codes."""
        components = {part.casefold() for part in path.parts}
        if classification in _STRONGLY_PROTECTED_CLASSES:
            return UserDataProtectionLevel.STRONGLY_PROTECTED, (
                f"classification-{classification.value}",
            )
        if any(path_is_within(path, root) for root in self._strong_roots):
            return UserDataProtectionLevel.STRONGLY_PROTECTED, ("user-library-path",)
        if path_is_within(path, self._roaming):
            return UserDataProtectionLevel.STRONGLY_PROTECTED, ("roaming-profile-path",)
        if components.intersection(_DEVELOPMENT_COMPONENTS):
            return UserDataProtectionLevel.STRONGLY_PROTECTED, ("development-environment",)
        if components.intersection(_HIGH_VALUE_COMPONENTS):
            return UserDataProtectionLevel.STRONGLY_PROTECTED, ("high-value-application-data",)
        if classification in _PROTECTED_CLASSES:
            return UserDataProtectionLevel.PROTECTED, (f"classification-{classification.value}",)
        if classification in {
            ResidualClassification.CACHE,
            ResidualClassification.LOG,
            ResidualClassification.TEMPORARY_DATA,
            ResidualClassification.CRASH_DUMP,
        }:
            return UserDataProtectionLevel.CAUTION, (f"classification-{classification.value}",)
        return UserDataProtectionLevel.CAUTION, ("ownership-does-not-imply-cleanup-safety",)
