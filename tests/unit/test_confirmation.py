from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.confirmation.models import ConfirmationState
from pc_manager_agent.confirmation.state_machine import ConfirmationError, ConfirmationService
from pc_manager_agent.domain.plans import PlanStep
from tests.unit.test_models import build_plan


def test_plan_confirmation_approval_and_rejection(tmp_path: Path) -> None:
    service = ConfirmationService()
    plan = build_plan(tmp_path)
    request = service.request_plan(plan, "scan one root")
    resolved = service.resolve(request.confirmation_id, True, plan)
    assert resolved.state is ConfirmationState.APPROVED
    service.require_plan_approved(plan)
    with pytest.raises(ConfirmationError, match="already"):
        service.resolve(request.confirmation_id, True, plan)

    other = build_plan(tmp_path)
    rejected = service.request_plan(other, "reject")
    result = service.resolve(rejected.confirmation_id, False, other)
    assert result.state is ConfirmationState.REJECTED
    with pytest.raises(ConfirmationError, match="not confirmed"):
        service.require_plan_approved(other)


def test_confirmation_rejects_unknown_expired_and_changed_plan(tmp_path: Path) -> None:
    current = [datetime(2026, 1, 1, tzinfo=UTC)]
    service = ConfirmationService(ttl_seconds=30, now=lambda: current[0])
    plan = build_plan(tmp_path)
    with pytest.raises(ConfirmationError, match="Unknown"):
        service.resolve(uuid4(), True, plan)
    request = service.request_plan(plan, "expires")
    current[0] += timedelta(seconds=31)
    with pytest.raises(ConfirmationError, match="expired"):
        service.resolve(request.confirmation_id, True, plan)

    current[0] = datetime(2026, 1, 1, tzinfo=UTC)
    fresh = service.request_plan(plan, "changed")
    changed = plan.model_copy(update={"summary": "changed"})
    with pytest.raises(ConfirmationError, match="does not match"):
        service.resolve(fresh.confirmation_id, True, changed)


def test_runtime_confirmation_binds_step_arguments(tmp_path: Path) -> None:
    service = ConfirmationService()
    plan = build_plan(tmp_path)
    with pytest.raises(ConfirmationError):
        service.request_runtime(plan, plan.steps[0], "runtime")
    plan_request = service.request_plan(plan, "plan")
    service.resolve(plan_request.confirmation_id, True, plan)
    runtime_request = service.request_runtime(plan, plan.steps[0], "runtime")
    changed_step = plan.steps[0].model_copy(update={"arguments": {"root": "changed"}})
    with pytest.raises(ConfirmationError, match="arguments"):
        service.resolve(runtime_request.confirmation_id, True, plan, changed_step)

    valid_request = service.request_runtime(plan, plan.steps[0], "runtime")
    service.resolve(valid_request.confirmation_id, True, plan, plan.steps[0])
    service.require_runtime_approved(plan, plan.steps[0])
    with pytest.raises(ConfirmationError):
        service.require_runtime_approved(plan, changed_step)


def test_runtime_confirmation_rejects_missing_or_wrong_step(tmp_path: Path) -> None:
    service = ConfirmationService()
    plan = build_plan(tmp_path)
    plan_request = service.request_plan(plan, "plan")
    service.resolve(plan_request.confirmation_id, True, plan)
    request = service.request_runtime(plan, plan.steps[0], "runtime")
    with pytest.raises(ConfirmationError, match="step"):
        service.resolve(request.confirmation_id, True, plan)
    wrong = PlanStep(
        **{
            **plan.steps[0].model_dump(),
            "step_id": "step-other",
        }
    )
    with pytest.raises(ConfirmationError, match="step"):
        service.resolve(request.confirmation_id, True, plan, wrong)
