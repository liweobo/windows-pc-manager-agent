from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pc_manager_agent.confirmation.software_uninstall_analysis import (
    SoftwareAnalysisConfirmationService,
)
from pc_manager_agent.domain.software_errors import SoftwareAnalysisError
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.orchestration.software_uninstall_analysis import (
    SoftwareUninstallAnalysisPlanCompiler,
)
from tests.fixtures.software_analysis import build_software_services, msi_entry


def test_plan_confirmation_is_digest_bound() -> None:
    service = SoftwareAnalysisConfirmationService()
    compiler = SoftwareUninstallAnalysisPlanCompiler()
    plan = compiler.compile("卸载软件 Example App")
    request = service.request_plan(plan)
    changed = plan.model_copy(update={"max_items": 10})
    with pytest.raises(SoftwareAnalysisError, match="changed"):
        service.resolve_plan(request.confirmation_id, True, changed)


def test_target_acknowledgement_expires_and_never_creates_execution_authority(
    tmp_path,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    clock = [now]
    services, repository, _platform = build_software_services(
        tmp_path / "audit.sqlite3", (msi_entry(),)
    )
    plan, review = services.service.prepare(
        "卸载软件 Example App",
        SoftwareTargetQuery(display_name="Example App"),
    )
    assert review.approved
    confirmation = services.service.request_plan_confirmation(plan)
    services.service.resolve_plan_confirmation(confirmation.confirmation_id, True, plan)
    outcome = services.service.analyze(plan)
    assert outcome.preview is not None
    local_confirmation = SoftwareAnalysisConfirmationService(
        plan_ttl_seconds=30,
        acknowledgement_ttl_seconds=1,
        now=lambda: clock[0],
    )
    local_plan_request = local_confirmation.request_plan(plan)
    local_confirmation.resolve_plan(local_plan_request.confirmation_id, True, plan)
    acknowledgement = local_confirmation.request_acknowledgement(plan, outcome.preview)
    clock[0] += timedelta(seconds=2)
    with pytest.raises(SoftwareAnalysisError, match="expired"):
        local_confirmation.resolve_acknowledgement(
            acknowledgement.acknowledgement_id, True, outcome.preview
        )
    assert not hasattr(acknowledgement, "authorization")
    repository.close()
