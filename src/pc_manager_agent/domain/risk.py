"""Risk and rollback classifications shared across all tools."""

from enum import StrEnum


class RiskLevel(StrEnum):
    """Deterministic operation risk classification."""

    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"

    @property
    def severity(self) -> int:
        """Return a sortable risk severity."""
        return int(self.value[1])


class RollbackLevel(StrEnum):
    """Truthful rollback capability classification."""

    FULL = "FULL"
    PARTIAL = "PARTIAL"
    MANUAL = "MANUAL"
    NONE = "NONE"
