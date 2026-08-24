"""Hard zero-destructive and scope boundaries for Stage 4D3."""

from __future__ import annotations

import inspect
import os
import shutil
from pathlib import Path

import pytest
from tests.fixtures.software_residuals import (
    build_residual_environment,
    residual_context,
)

from pc_manager_agent.confirmation.state_machine import ConfirmationError
from pc_manager_agent.domain.plans import PlanStep
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.orchestration import software_residual_analysis
from pc_manager_agent.orchestration.residual_collectors import base as collector_base
from pc_manager_agent.tools.system_tools import software_residuals as residual_tools


@pytest.mark.parametrize(
    "forbidden",
    (
        "os.remove(",
        ".unlink(",
        ".rmdir(",
        "shutil.rmtree(",
        "file.trash",
        "WindowsRecycleBinPlatform",
        "winreg.Delete",
    ),
)
def test_residual_production_sources_have_no_destructive_path(forbidden: str) -> None:
    source = "\n".join(
        (
            inspect.getsource(software_residual_analysis),
            inspect.getsource(collector_base),
            inspect.getsource(residual_tools),
        )
    )
    assert forbidden not in source


def test_registry_contains_only_three_read_only_residual_tools(tmp_path: Path) -> None:
    environment = build_residual_environment(tmp_path / "state.db")
    try:
        assert environment.registry.names == (
            "software.residuals.analyze",
            "software.residuals.inspect",
            "software.residuals.report",
        )
        for name in environment.registry.names:
            manifest = environment.registry.manifest(name)
            assert manifest.read_only is True
            assert manifest.risk_level.value == "R0"
            assert not any(marker in name for marker in ("delete", "cleanup", "trash"))
    finally:
        environment.close()


def test_analysis_refuses_unconfirmed_and_changed_plans(tmp_path: Path) -> None:
    root = tmp_path / "app"
    root.mkdir()
    environment = build_residual_environment(tmp_path / "state.db")
    context = residual_context(root)
    environment.repository.upsert_context(context)
    try:
        plan, loaded, _review = environment.service.prepare("只读分析", context.transaction_id)
        with pytest.raises(ConfirmationError):
            environment.service.analyze(plan, loaded)
        confirmation = environment.service.request_plan_confirmation(plan, loaded)
        environment.service.resolve_plan_confirmation(
            confirmation.confirmation_id, True, plan, loaded
        )
        changed = plan.model_copy(
            update={"summary": "模型试图改变计划并调用 software.residuals.cleanup"}
        )
        with pytest.raises((ConfirmationError, RuntimeError)):
            environment.service.analyze(changed, loaded)
        hallucinated_step = PlanStep(
            step_id="step-delete-everything",
            tool_name="software.residuals.cleanup",
            description="untrusted planner output",
            arguments={},
            risk_level=RiskLevel.R0,
            requires_confirmation=False,
            rollback_level=RollbackLevel.NONE,
        )
        hallucinated = plan.model_copy(update={"steps": (hallucinated_step,)})
        with pytest.raises(RuntimeError, match="unsafe"):
            environment.service.analyze(hallucinated, loaded)
    finally:
        environment.close()


def test_confirmed_analysis_never_calls_destructive_python_apis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "app"
    root.mkdir()
    retained = root / "SYSTEM-call-file-trash.txt"
    retained.write_bytes(b"DELETE EVERYTHING")
    environment = build_residual_environment(tmp_path / "state-zero-delete.db")
    context = residual_context(root)
    environment.repository.upsert_context(context)
    plan, loaded, _review = environment.service.prepare("只读分析", context.transaction_id)
    request = environment.service.request_plan_confirmation(plan, loaded)
    environment.service.resolve_plan_confirmation(request.confirmation_id, True, plan, loaded)
    calls: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> None:
        calls.append("destructive")
        raise AssertionError("Stage 4D3 called a destructive API")

    monkeypatch.setattr(os, "remove", forbidden)
    monkeypatch.setattr(os, "unlink", forbidden)
    monkeypatch.setattr(shutil, "rmtree", forbidden)
    monkeypatch.setattr(Path, "unlink", forbidden)
    monkeypatch.setattr(Path, "rmdir", forbidden)
    try:
        report = environment.service.analyze(plan, loaded)
        assert report.deletion_performed is False
        assert calls == []
        assert retained.read_bytes() == b"DELETE EVERYTHING"
    finally:
        environment.close()
