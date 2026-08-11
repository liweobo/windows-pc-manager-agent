"""Typed failures used by the read-only file analysis workflow."""


class FileAnalysisError(RuntimeError):
    """Base failure for a Stage 1 file analysis operation."""


class PathNotAuthorizedError(FileAnalysisError, PermissionError):
    """Raised when a requested path is not within an authorized root."""


class PathBlockedError(FileAnalysisError, PermissionError):
    """Raised when a path enters a protected or user-forbidden root."""


class PathUnavailableError(FileAnalysisError, FileNotFoundError):
    """Raised when a requested path cannot be safely inspected."""


class ScanCancelledError(FileAnalysisError):
    """Reserved for callers that require cancellation as an exception."""


class FileChangedDuringScanError(FileAnalysisError):
    """Raised when a file identity or relevant metadata changes during analysis."""


class HashingError(FileAnalysisError):
    """Raised when a candidate file cannot be safely hashed."""
