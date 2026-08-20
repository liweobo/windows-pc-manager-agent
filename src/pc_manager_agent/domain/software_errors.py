"""Stable public errors for Stage 4D1 software analysis."""

from enum import StrEnum


class SoftwareAnalysisErrorCode(StrEnum):
    """Finite failure codes suitable for UI and redacted audit records."""

    TARGET_NOT_FOUND = "target_not_found"
    TARGET_AMBIGUOUS = "target_ambiguous"
    TARGET_CHANGED = "target_changed"
    CAPABILITY_CHANGED = "capability_changed"
    PREVIEW_EXPIRED = "preview_expired"
    PROTECTED_SOFTWARE = "protected_software"
    UNSUPPORTED_METADATA = "unsupported_metadata"
    INVENTORY_UNAVAILABLE = "inventory_unavailable"
    AUDIT_UNAVAILABLE = "audit_unavailable"
    CANCELLED = "cancelled"
    ZERO_EXECUTION_VIOLATION = "zero_execution_violation"


class SoftwareAnalysisError(RuntimeError):
    """Error with a stable code and beginner-readable message."""

    def __init__(self, code: SoftwareAnalysisErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
