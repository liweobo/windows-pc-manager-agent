"""Risk and rollback classifications shared across all tools."""

from enum import StrEnum


class RiskLevel(StrEnum):
    """Deterministic operation risk classification."""

    """
    R0：只读
    R1：低风险、可恢复
    R2：破坏性但可能恢复
    R3：高风险系统修改，MVP 不执行
    R4：禁止操作
    """
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R2_HIGH_IMPACT = "R2_HIGH_IMPACT"
    R3 = "R3"
    R4 = "R4"

    @property
    def severity(self) -> int:
        """Return a sortable risk severity."""
        return {
            RiskLevel.R0: 0,
            RiskLevel.R1: 1,
            RiskLevel.R2: 2,
            RiskLevel.R2_HIGH_IMPACT: 3,
            RiskLevel.R3: 4,
            RiskLevel.R4: 5,
        }[self]


class RollbackLevel(StrEnum):
    """Truthful rollback capability classification."""

    FULL = "FULL"
    PARTIAL = "PARTIAL"
    MANUAL = "MANUAL"
    NONE = "NONE"
