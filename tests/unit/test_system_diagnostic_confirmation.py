from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pc_manager_agent.confirmation.system_diagnostics import (
    DiagnosticConfirmationError,
    DiagnosticConfirmationService,
)
from pc_manager_agent.domain.system_diagnostics import (
    DiagnosticIntent,
    DiagnosticPlan,
    SystemCollector,
)


def _plan() -> DiagnosticPlan:
    return DiagnosticPlan(
        summary="cpu",
        user_goal="cpu",
        intent=DiagnosticIntent.CPU,
        collectors=(SystemCollector.SYSTEM_INFO, SystemCollector.CPU),
    )


def test_confirmation_binds_digest_and_is_single_use() -> None:
    service = DiagnosticConfirmationService()
    plan = _plan()
    request = service.request(plan)
    with pytest.raises(DiagnosticConfirmationError, match="changed"):
        service.resolve(request.confirmation_id, True, plan.model_copy(update={"sample_count": 4}))
    service.resolve(request.confirmation_id, True, plan)
    service.require_approved(plan)
    with pytest.raises(DiagnosticConfirmationError, match="already resolved"):
        service.resolve(request.confirmation_id, True, plan)


def test_confirmation_expiry_and_rejection_fail_closed() -> None:
    current = datetime(2026, 1, 1, tzinfo=UTC)

    def now() -> datetime:
        return current

    service = DiagnosticConfirmationService(ttl_seconds=2, now=now)
    plan = _plan()
    rejected = service.request(plan)
    service.resolve(rejected.confirmation_id, False, plan)
    with pytest.raises(DiagnosticConfirmationError, match="not confirmed"):
        service.require_approved(plan)
    expiring = service.request(plan)
    current += timedelta(seconds=3)
    with pytest.raises(DiagnosticConfirmationError, match="expired"):
        service.resolve(expiring.confirmation_id, True, plan)
