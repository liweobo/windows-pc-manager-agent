from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import BaseModel, ValidationError

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.software_errors import SoftwareAnalysisError
from pc_manager_agent.domain.software_uninstall_analysis import (
    ResolvedSoftwareTarget,
    SoftwareAnalysisOutcome,
    SoftwareCapabilityRequest,
    SoftwarePreviewRequest,
    SoftwareTargetQuery,
    SoftwareUninstallAnalysisPlan,
    SoftwareUninstallPreview,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareArchitecture, SoftwareScope
from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
from pc_manager_agent.orchestration.software_inventory import (
    SoftwareInventoryService,
    SoftwareInventorySnapshot,
)
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.safety.software_uninstall_validator import (
    SoftwareUninstallSafetyValidator,
)
from pc_manager_agent.safety.software_zero_execution import SoftwareZeroExecutionGuard
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.software_analysis import (
    SoftwareInspectTool,
    SoftwareInventoryTool,
    SoftwareResolveTool,
    SoftwareUninstallCapabilityTool,
    SoftwareUninstallPreviewTool,
)
from tests.fixtures.software_analysis import (
    FakeSoftwareInventoryPlatform,
    build_software_services,
    msi_entry,
)


def _valid_preview(
    tmp_path: Path,
) -> tuple[SoftwareUninstallAnalysisPlan, SoftwareUninstallPreview]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    services, repository, _platform = build_software_services(
        tmp_path / "audit.sqlite3", (msi_entry(),)
    )
    plan, _review = services.service.prepare(
        "卸载软件 Example App", SoftwareTargetQuery(display_name="Example App")
    )
    request = services.service.request_plan_confirmation(plan)
    services.service.resolve_plan_confirmation(request.confirmation_id, True, plan)
    outcome = services.service.analyze(plan)
    assert outcome.preview is not None
    repository.close()
    return plan, outcome.preview


def test_resolved_target_and_outcome_models_reject_contradictions(tmp_path: Path) -> None:
    plan, preview = _valid_preview(tmp_path)
    selected = preview.target
    query = SoftwareTargetQuery(display_name="Example App")
    with pytest.raises(ValidationError, match="cannot also be ambiguous"):
        ResolvedSoftwareTarget(
            query=query,
            selected=selected,
            candidates=(selected,),
            ambiguous=True,
            reason="invalid",
        )
    with pytest.raises(ValidationError, match="must be marked ambiguous"):
        ResolvedSoftwareTarget(query=query, reason="invalid")
    ambiguous = ResolvedSoftwareTarget(query=query, ambiguous=True, reason="ambiguous")
    with pytest.raises(ValidationError, match="cannot have a Preview"):
        SoftwareAnalysisOutcome(resolution=ambiguous, preview=preview)
    exact = ResolvedSoftwareTarget(query=query, selected=selected, reason="exact")
    with pytest.raises(ValidationError, match="requires a Preview"):
        SoftwareAnalysisOutcome(resolution=exact)
    changed_preview = preview.model_copy(update={"identity_digest": "f" * 64})
    with pytest.raises(ValidationError, match="identity digest"):
        SoftwareAnalysisOutcome(resolution=exact, preview=changed_preview)
    assert plan.estimated_system_changes == 0


@pytest.mark.parametrize(
    "updates",
    (
        {"risk_level": RiskLevel.R1},
        {"requires_plan_confirmation": False},
        {"requires_runtime_confirmation": True},
        {"rollback_level": RollbackLevel.MANUAL},
        {"estimated_system_changes": 1},
        {"tool_names": ("software.inventory", "software.inventory")},
    ),
)
def test_plan_model_rejects_weakened_zero_execution_contract(updates: dict[str, object]) -> None:
    values: dict[str, object] = {
        "user_goal": "卸载软件 Example App",
        "summary": "read only",
        "target_query": SoftwareTargetQuery(display_name="Example App"),
        "tool_names": ("software.inventory",),
    }
    values.update(updates)
    with pytest.raises(ValidationError):
        SoftwareUninstallAnalysisPlan(**values)


def test_preview_model_rejects_all_changed_bindings_and_expiry(tmp_path: Path) -> None:
    _plan, preview = _valid_preview(tmp_path)
    base = preview.model_dump(mode="python")
    cases = (
        {"execution_performed": True},
        {"analysis_risk_level": RiskLevel.R1},
        {"analysis_rollback_level": RollbackLevel.MANUAL},
        {"expires_at": preview.generated_at},
        {"identity_digest": "1" * 64},
        {"metadata_digest": "2" * 64},
        {"capability_digest": "3" * 64},
    )
    for update in cases:
        values = dict(base)
        values.update(update)
        with pytest.raises(ValidationError):
            SoftwareUninstallPreview.model_validate(values)


def test_target_resolver_filters_every_exact_field_and_inspects_missing() -> None:
    entries = (
        msi_entry(version="1.0"),
        msi_entry(
            version="2.0",
            product_code="{22345678-1234-1234-1234-1234567890AB}",
            architecture=SoftwareArchitecture.X86,
        ),
    )
    resolver = SoftwareTargetResolver(
        SoftwareInventoryService(FakeSoftwareInventoryPlatform(entries))
    )
    resolved, _snapshot = resolver.resolve(
        SoftwareTargetQuery(
            display_name="Example App",
            publisher="Example Publisher",
            display_version="2.0",
            scope=SoftwareScope.CURRENT_USER,
            architecture=SoftwareArchitecture.X86,
        ),
        100,
        CancellationToken(),
    )
    assert resolved.selected is not None
    missing, _ = resolver.inspect("0" * 64, 100, CancellationToken())
    assert missing is None


def test_target_resolver_fails_closed_for_constructed_query_without_selector() -> None:
    resolver = SoftwareTargetResolver(
        SoftwareInventoryService(FakeSoftwareInventoryPlatform((msi_entry(),)))
    )
    invalid_query = SoftwareTargetQuery.model_construct(identity_digest=None, display_name=None)
    with pytest.raises(SoftwareAnalysisError, match="no exact identity or display name"):
        resolver.resolve(invalid_query, 100, CancellationToken())


def test_inventory_duplicate_conflict_and_legacy_projection() -> None:
    raw = msi_entry()
    duplicate = raw.model_copy(update={"uninstall_string": "different metadata"})
    service = SoftwareInventoryService(FakeSoftwareInventoryPlatform((raw, duplicate)))
    snapshot = service.collect(100, CancellationToken())
    assert len(snapshot.inventory.entries) == 1
    assert any("conflicting" in warning for warning in snapshot.inventory.warnings)
    legacy, warnings, truncated = service.project_legacy(100, CancellationToken())
    assert len(legacy) == 1
    assert warnings
    assert not truncated


def test_validator_reports_constructed_plan_and_preview_tampering(tmp_path: Path) -> None:
    services, repository, _platform = build_software_services(
        tmp_path / "audit.sqlite3", (msi_entry(),)
    )
    plan, preview = _valid_preview(tmp_path / "second")
    validator = SoftwareUninstallSafetyValidator(services.registry, SoftwareZeroExecutionGuard())
    invalid_plan = plan.model_copy(
        update={
            "tool_names": ("software.inventory",),
            "risk_level": RiskLevel.R1,
            "rollback_level": RollbackLevel.MANUAL,
            "estimated_system_changes": 1,
        }
    )
    review = validator.review_plan(invalid_plan)
    assert {issue.code for issue in review.issues} >= {"tool-set", "risk", "impact"}
    invalid_preview = preview.model_copy(
        update={
            "plan_digest": "0" * 64,
            "execution_performed": True,
            "analysis_risk_level": RiskLevel.R1,
            "identity_digest": "1" * 64,
            "capability_digest": "2" * 64,
            "stop_reason": "",
        }
    )
    preview_review = validator.review_preview(plan, invalid_preview)
    assert {issue.code for issue in preview_review.issues} == {
        "plan-digest",
        "execution",
        "risk",
        "identity",
        "capability",
        "stop",
    }
    repository.close()


def test_zero_guard_rejects_wrong_registry_manifest_and_result(tmp_path: Path) -> None:
    services, repository, _platform = build_software_services(
        tmp_path / "audit.sqlite3", (msi_entry(),)
    )
    guard = SoftwareZeroExecutionGuard()

    class UnexpectedResult(BaseModel):
        value: str = "unexpected"

    with pytest.raises(SoftwareAnalysisError, match="zero execution"):
        guard.validate_result(UnexpectedResult())
    with pytest.raises(SoftwareAnalysisError, match="allow-list"):
        guard.validate_registry(ToolRegistry())
    manifest = services.registry.manifest("software.inventory")
    object.__setattr__(manifest, "read_only", False)
    with pytest.raises(SoftwareAnalysisError, match="not strictly read-only"):
        guard.validate_registry(services.registry)
    repository.close()


def test_all_tools_reject_wrong_direct_input(tmp_path: Path) -> None:
    services, repository, _platform = build_software_services(
        tmp_path / "audit.sqlite3", (msi_entry(),)
    )
    resolver = services.resolver

    class UnexpectedInput(BaseModel):
        value: str = "unexpected"

    tools = (
        SoftwareInventoryTool(services.inventory),
        SoftwareResolveTool(resolver),
        SoftwareInspectTool(resolver),
        SoftwareUninstallCapabilityTool(resolver, UninstallCapabilityResolver()),
        SoftwareUninstallPreviewTool(resolver, Mock()),
    )
    for tool in tools:
        with pytest.raises(TypeError, match="unexpected input"):
            tool.execute(UnexpectedInput(), CancellationToken())
    repository.close()


def test_capability_and_preview_tools_fail_when_fresh_evidence_disappears(tmp_path: Path) -> None:
    raw = msi_entry()
    inventory = SoftwareInventoryService(FakeSoftwareInventoryPlatform((raw,)))
    snapshot = inventory.collect(100, CancellationToken())
    target = snapshot.inventory.entries[0]
    identity_digest = target.identity.canonical_digest()
    plan = SoftwareUninstallAnalysisPlan(
        user_goal="卸载软件 Example App",
        summary="read-only analysis",
        target_query=SoftwareTargetQuery(identity_digest=identity_digest),
        tool_names=("software.inventory",),
    )

    missing_resolver = Mock()
    missing_resolver.inspect.return_value = (None, snapshot)
    capability_tool = SoftwareUninstallCapabilityTool(
        missing_resolver, UninstallCapabilityResolver()
    )
    preview_tool = SoftwareUninstallPreviewTool(missing_resolver, Mock())
    with pytest.raises(SoftwareAnalysisError, match="disappeared or changed"):
        capability_tool.execute(
            SoftwareCapabilityRequest(identity_digest=identity_digest), CancellationToken()
        )
    with pytest.raises(SoftwareAnalysisError, match="disappeared or changed"):
        preview_tool.execute(
            SoftwarePreviewRequest(plan=plan, identity_digest=identity_digest),
            CancellationToken(),
        )

    no_raw_snapshot = SoftwareInventorySnapshot(inventory=snapshot.inventory, raw_by_identity={})
    missing_resolver.inspect.return_value = (target, no_raw_snapshot)
    with pytest.raises(SoftwareAnalysisError, match="Raw source evidence"):
        capability_tool.execute(
            SoftwareCapabilityRequest(identity_digest=identity_digest), CancellationToken()
        )
    with pytest.raises(SoftwareAnalysisError, match="Raw source evidence"):
        preview_tool.execute(
            SoftwarePreviewRequest(plan=plan, identity_digest=identity_digest),
            CancellationToken(),
        )


def test_validator_fails_closed_when_registry_allow_list_is_missing() -> None:
    validator = SoftwareUninstallSafetyValidator(ToolRegistry(), SoftwareZeroExecutionGuard())
    plan = SoftwareUninstallAnalysisPlan(
        user_goal="卸载软件 Example App",
        summary="read-only analysis",
        target_query=SoftwareTargetQuery(display_name="Example App"),
        tool_names=("software.inventory",),
    )
    review = validator.review_plan(plan)
    assert not review.approved
    assert any(issue.code == "registry" for issue in review.issues)
