"""Typed failures for controlled process-management workflows."""

from __future__ import annotations

from pc_manager_agent.domain.process_actions import ProcessActionErrorCode


class ProcessActionError(RuntimeError):
    """Base failure with a stable error code suitable for audit and UI rendering."""

    def __init__(self, code: ProcessActionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProcessIdentityChangedError(ProcessActionError):
    """Raised when a PID no longer identifies the exact Previewed process."""

    def __init__(self, message: str = "Process identity changed; generate a new Preview") -> None:
        super().__init__(ProcessActionErrorCode.PROCESS_IDENTITY_CHANGED, message)


class ProtectedProcessError(ProcessActionError):
    """Raised when deterministic safety policy blocks a process."""


class UnsupportedGracefulExitError(ProcessActionError):
    """Raised when no target-owned top-level window can receive WM_CLOSE."""

    def __init__(self, message: str = "The target has no supported graceful-exit window") -> None:
        super().__init__(ProcessActionErrorCode.UNSUPPORTED_GRACEFUL_EXIT, message)


class GracefulExitTimeoutError(ProcessActionError):
    """Raised only after a verified graceful-exit timeout."""

    def __init__(
        self, message: str = "The target remained active after the graceful timeout"
    ) -> None:
        super().__init__(ProcessActionErrorCode.GRACEFUL_EXIT_TIMEOUT, message)


class ProcessAccessDeniedError(ProcessActionError):
    """Raised when ordinary-user rights cannot inspect or manage a target."""

    def __init__(self, message: str = "Current ordinary-user permissions are insufficient") -> None:
        super().__init__(ProcessActionErrorCode.PROCESS_ACCESS_DENIED, message)


class ProcessAlreadyExitedError(ProcessActionError):
    """Raised when the original identity exited before mutation began."""

    def __init__(
        self, message: str = "The process already exited; no action was performed"
    ) -> None:
        super().__init__(ProcessActionErrorCode.PROCESS_ALREADY_EXITED, message)


class ProcessTargetResolutionError(ProcessActionError):
    """Raised for absent or ambiguous local process target queries."""
