from __future__ import annotations

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.software_uninstall_analysis import (
    SoftwareInventoryResult,
    SoftwareTargetQuery,
)
from pc_manager_agent.safety.software_zero_execution import STAGE_4D1_TOOL_NAMES
from tests.fixtures.software_analysis import build_software_services, msi_entry


def test_exact_stage4d1_registry_is_read_only_and_results_prove_zero_execution(tmp_path) -> None:
    services, repository, _platform = build_software_services(
        tmp_path / "audit.sqlite3", (msi_entry(),)
    )
    assert services.registry.names == tuple(sorted(STAGE_4D1_TOOL_NAMES))
    for name in services.registry.names:
        manifest = services.registry.manifest(name)
        assert manifest.read_only
        assert manifest.risk_level is RiskLevel.R0
        assert manifest.rollback_level is RollbackLevel.NONE
        assert not manifest.requires_runtime_confirmation
    result = services.registry.execute("software.inventory", {"max_items": 10})
    assert isinstance(result, SoftwareInventoryResult)
    assert result.execution_performed is False
    repository.close()


def test_service_uses_all_five_tools_and_stops_after_preview(tmp_path) -> None:
    services, repository, platform = build_software_services(
        tmp_path / "audit.sqlite3", (msi_entry(),)
    )
    plan, review = services.service.prepare(
        "卸载软件 Example App", SoftwareTargetQuery(display_name="Example App")
    )
    assert review.approved
    confirmation = services.service.request_plan_confirmation(plan)
    services.service.resolve_plan_confirmation(confirmation.confirmation_id, True, plan)
    outcome = services.service.analyze(plan)
    assert outcome.preview is not None
    assert outcome.preview.execution_performed is False
    assert outcome.preview.executable_in_current_stage is False
    assert platform.calls >= 5
    acknowledgement = services.service.request_target_acknowledgement(plan, outcome.preview)
    resolved = services.service.resolve_target_acknowledgement(
        acknowledgement.acknowledgement_id, True, plan, outcome.preview
    )
    assert resolved.state.value == "acknowledged"
    recent = repository.list_recent(20)
    assert any(row.event_type == "software.target_acknowledgement.resolved" for row in recent)
    repository.close()
