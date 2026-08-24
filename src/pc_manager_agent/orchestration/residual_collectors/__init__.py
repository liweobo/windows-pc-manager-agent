"""Finite read-only collector set for Stage 4D3."""

from pc_manager_agent.orchestration.residual_collectors.base import (
    CollectorResult,
    ResidualCollectionBudget,
    ResidualCollector,
)
from pc_manager_agent.orchestration.residual_collectors.sources import (
    InstallLocationResidualCollector,
    KnownAppDataResidualCollector,
    KnownConfigurationResidualCollector,
    KnownServiceArtifactCollector,
    MsixDataResidualCollector,
    ShortcutResidualCollector,
)

__all__ = [
    "CollectorResult",
    "InstallLocationResidualCollector",
    "KnownAppDataResidualCollector",
    "KnownConfigurationResidualCollector",
    "KnownServiceArtifactCollector",
    "MsixDataResidualCollector",
    "ResidualCollectionBudget",
    "ResidualCollector",
    "ShortcutResidualCollector",
]
