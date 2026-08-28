from __future__ import annotations

from pathlib import Path

import pytest
from tests.fixtures.system_optimization import build_optimization_registry

from pc_manager_agent.domain.system_optimization import OptimizationToolName
from pc_manager_agent.safety.system_optimization import (
    OptimizationAuthorityError,
    reject_stage4e1_execution_authority,
)
from pc_manager_agent.tools.registry import UnknownToolError


def test_reports_and_candidate_ids_are_never_execution_authority() -> None:
    for artifact in ("old-report-id", "candidate-id", {"selected": True}):
        with pytest.raises(OptimizationAuthorityError, match="report-only"):
            reject_stage4e1_execution_authority(artifact)


@pytest.mark.parametrize(
    "name",
    (
        "optimization.clean",
        "optimization.fix",
        "optimization.boost",
        "optimization.apply",
        "system.clean.all",
        "software.residuals.trash",
        "system.service.stop",
    ),
)
def test_mutation_and_broker_routes_are_not_registered(name: str) -> None:
    registry = build_optimization_registry()
    with pytest.raises(UnknownToolError):
        registry.manifest(name)
    assert all(item.value in registry.names for item in OptimizationToolName)


def test_stage4e1_modules_do_not_import_shell_or_mutation_adapters() -> None:
    root = Path(__file__).resolve().parents[2] / "src/pc_manager_agent"
    files = (
        root / "tools/system_tools/system_optimization.py",
        root / "orchestration/system_optimization.py",
        root / "platform_support/windows/system_optimization.py",
    )
    forbidden = (
        "import subprocess",
        "from subprocess",
        "shell=True",
        "WindowsRecycleBinPlatform",
        "PrivilegedBroker",
        "ServiceControlPlatform",
        "ProcessManagementPlatform",
        "StartupManagementPlatform",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert not any(token in combined for token in forbidden)
