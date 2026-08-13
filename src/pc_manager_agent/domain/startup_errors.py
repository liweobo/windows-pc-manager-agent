"""Typed failures for controlled startup-management workflows."""

from __future__ import annotations

from pc_manager_agent.domain.startup_actions import StartupErrorCode


class StartupActionError(RuntimeError):
    """Workflow failure carrying a stable audit and user-interface code."""

    def __init__(self, code: StartupErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class StartupIdentityChangedError(StartupActionError):
    """Raised when the live object no longer matches Preview or confirmation."""

    def __init__(self, message: str = "Startup entry changed; generate a new Preview") -> None:
        super().__init__(StartupErrorCode.IDENTITY_CHANGED, message)


class StartupBackupError(StartupActionError):
    """Raised when exact restore material is absent, corrupt, or unverifiable."""


class StartupConflictError(StartupActionError):
    """Raised when disabling or restoring would overwrite a current object."""

    def __init__(self, message: str = "A conflicting startup entry already exists") -> None:
        super().__init__(StartupErrorCode.DESTINATION_CONFLICT, message)
