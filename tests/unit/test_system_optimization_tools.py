from __future__ import annotations

import pytest

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.system_optimization import OptimizationToolName
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import UnknownToolError
from pc_manager_agent.tools.system_tools.system_optimization import OptimizationSnapshotTool
from tests.fixtures.system_optimization import FakeOptimizationPlatform, build_optimization_registry


def test_registry_contains_exactly_five_read_only_tools() -> None:
    registry = build_optimization_registry()
    assert registry.names == tuple(sorted(item.value for item in OptimizationToolName))
    for name in registry.names:
        manifest = registry.manifest(name)
        assert manifest.risk_level is RiskLevel.R0
        assert manifest.read_only is True
        assert manifest.rollback_level is RollbackLevel.NONE
        assert manifest.requires_runtime_confirmation is False
    with pytest.raises(UnknownToolError):
        registry.execute("optimization.clean", {})


def test_tool_rejects_direct_wrong_input() -> None:
    tool = OptimizationSnapshotTool(FakeOptimizationPlatform())
    with pytest.raises(TypeError, match="unexpected input"):
        tool.execute(object(), CancellationToken())
