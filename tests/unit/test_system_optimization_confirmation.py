from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from pc_manager_agent.confirmation.system_optimization import (
    OptimizationConfirmationError,
    OptimizationConfirmationService,
)
from pc_manager_agent.domain.system_optimization import (
    OptimizationGoal,
    OptimizationPlan,
    OptimizationToolName,
)


def _plan() -> OptimizationPlan:
    return OptimizationPlan(
        user_goal="general health check",
        summary="read only",
        goals=(OptimizationGoal.GENERAL_HEALTH_CHECK,),
        tools=tuple(OptimizationToolName),
    )


def test_confirmation_binds_digest_and_rejects_reuse() -> None:
    service = OptimizationConfirmationService()
    plan = _plan()
    request = service.request(plan)
    changed = plan.model_copy(update={"inactive_days": 120})
    with pytest.raises(OptimizationConfirmationError, match="changed"):
        service.resolve(request.confirmation_id, True, changed)
    service.resolve(request.confirmation_id, True, plan)
    service.require_approved(plan)
    with pytest.raises(OptimizationConfirmationError, match="already resolved"):
        service.resolve(request.confirmation_id, True, plan)


def test_confirmation_expiry_fails_closed() -> None:
    current = datetime(2026, 1, 1, tzinfo=UTC)

    def now() -> datetime:
        return current

    service = OptimizationConfirmationService(ttl_seconds=2, now=now)
    plan = _plan()
    request = service.request(plan)
    current += timedelta(seconds=3)
    with pytest.raises(OptimizationConfirmationError, match="expired"):
        service.resolve(request.confirmation_id, True, plan)


def test_unknown_rejected_and_changed_approval_fail_closed() -> None:
    service = OptimizationConfirmationService()
    plan = _plan()
    with pytest.raises(OptimizationConfirmationError, match="Unknown"):
        service.resolve(uuid4(), True, plan)
    rejected = service.request(plan)
    service.resolve(rejected.confirmation_id, False, plan)
    with pytest.raises(OptimizationConfirmationError, match="not confirmed"):
        service.require_approved(plan)
    approved = service.request(plan)
    service.resolve(approved.confirmation_id, True, plan)
    with pytest.raises(OptimizationConfirmationError, match="changed"):
        service.require_approved(plan.model_copy(update={"inactive_days": 91}))
