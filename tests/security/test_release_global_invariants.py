"""Release-blocking static invariants across every production Python module."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pc_manager_agent.config.production import FeatureFlags, ReleaseFeature
from pc_manager_agent.release.gate import ReadinessLevel, ReleaseCheck, ReleaseGateEvaluator

_ROOT = Path(__file__).resolve().parents[2]
_SOURCE = _ROOT / "src" / "pc_manager_agent"


def _call_name(call: ast.Call) -> str:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
        return f"{function.value.id}.{function.attr}"
    return ""


@pytest.mark.security
def test_production_source_has_no_dynamic_code_or_unsafe_shell_primitive() -> None:
    forbidden_calls = {"eval", "exec", "os.system", "pickle.load", "pickle.loads"}
    violations: list[str] = []
    for path in _SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name in forbidden_calls:
                violations.append(f"{path.relative_to(_ROOT)}:{node.lineno}:{name}")
            if name.startswith("subprocess.") and any(
                keyword.arg == "shell"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in node.keywords
            ):
                violations.append(f"{path.relative_to(_ROOT)}:{node.lineno}:shell=True")

    assert violations == []


@pytest.mark.security
def test_private_rc_is_a_closed_read_only_surface_and_gate_requires_invariants() -> None:
    assert FeatureFlags.private_rc_defaults().enabled == {
        ReleaseFeature.FILE_ANALYSIS,
        ReleaseFeature.SYSTEM_DIAGNOSTICS,
        ReleaseFeature.SOFTWARE_ANALYSIS,
        ReleaseFeature.OPTIMIZATION_ANALYSIS,
    }
    for feature in FeatureFlags.private_rc_defaults().enabled:
        assert "ACTION" not in feature.name
        assert "CLEANUP" not in feature.name
        assert "UNINSTALL" not in feature.name
        assert feature is not ReleaseFeature.PRIVILEGED_BROKER
    assert ReleaseCheck.GLOBAL_INVARIANTS in ReleaseGateEvaluator.required_checks(
        ReadinessLevel.PRIVATE_RC_READY
    )
