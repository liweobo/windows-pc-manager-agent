"""Expiry, binding, tier, and replay tests for Stage 4D2B confirmations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from pc_manager_agent.confirmation.vendor_uninstall import (
    VendorUninstallConfirmation,
    VendorUninstallConfirmationError,
    VendorUninstallConfirmationService,
    VendorUninstallConfirmationState,
)
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.domain.vendor_uninstall import VendorUninstallPlan, VendorUninstallPreview
from tests.fixtures.vendor_uninstall import build_vendor_environment


class _Store:
    def __init__(self) -> None:
        self.confirmations: dict[UUID, VendorUninstallConfirmation] = {}
        self.consumed_pairs = 0

    def save_plan_confirmation(self, confirmation: VendorUninstallConfirmation) -> None:
        self.confirmations[confirmation.confirmation_id] = confirmation

    def save_runtime_confirmation(
        self,
        confirmation: VendorUninstallConfirmation,
        preview: VendorUninstallPreview,
    ) -> None:
        self.confirmations[confirmation.confirmation_id] = confirmation

    def get_confirmation(self, confirmation_id: UUID) -> VendorUninstallConfirmation:
        return self.confirmations[confirmation_id]

    def resolve_confirmation(self, confirmation: VendorUninstallConfirmation) -> None:
        self.confirmations[confirmation.confirmation_id] = confirmation

    def consume_confirmation_pair(
        self,
        plan_confirmation: VendorUninstallConfirmation,
        runtime_confirmation: VendorUninstallConfirmation,
    ) -> None:
        self.consumed_pairs += 1
        self.confirmations[plan_confirmation.confirmation_id] = plan_confirmation.model_copy(
            update={"state": VendorUninstallConfirmationState.CONSUMED}
        )
        self.confirmations[runtime_confirmation.confirmation_id] = runtime_confirmation.model_copy(
            update={"state": VendorUninstallConfirmationState.CONSUMED}
        )


def _plan_preview(tmp_path: Path) -> tuple[object, VendorUninstallPlan, VendorUninstallPreview]:
    environment = build_vendor_environment(tmp_path / "state.sqlite3")
    prepared = environment.services.service.prepare(
        "卸载 Example App",
        SoftwareTargetQuery(display_name="Example App"),
    )
    assert prepared.plan is not None and prepared.preview is not None
    return environment, prepared.plan, prepared.preview


def test_confirmation_service_rejects_nonpositive_ttl() -> None:
    with pytest.raises(ValueError, match="TTLs"):
        VendorUninstallConfirmationService(_Store(), plan_ttl_seconds=0)


def test_confirmation_expiry_is_persisted_and_rejected(tmp_path: Path) -> None:
    environment, plan, preview = _plan_preview(tmp_path)
    store = _Store()
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    service = VendorUninstallConfirmationService(
        store,
        plan_ttl_seconds=1,
        now=lambda: now[0],
    )
    try:
        confirmation = service.request_plan(plan, preview)
        now[0] += timedelta(seconds=2)
        with pytest.raises(VendorUninstallConfirmationError, match="expired"):
            service.resolve_plan(confirmation.confirmation_id, True, plan, preview)
        assert store.get_confirmation(confirmation.confirmation_id).state is (
            VendorUninstallConfirmationState.EXPIRED
        )
    finally:
        environment.close()


def test_runtime_requires_approved_unchanged_parent(tmp_path: Path) -> None:
    environment, plan, preview = _plan_preview(tmp_path)
    store = _Store()
    service = VendorUninstallConfirmationService(store)
    try:
        pending = service.request_plan(plan, preview)
        with pytest.raises(VendorUninstallConfirmationError, match="Approved"):
            service.request_runtime(pending.confirmation_id, plan, preview)

        approved = service.resolve_plan(pending.confirmation_id, True, plan, preview)
        changed = preview.model_copy(
            update={"preflight": preview.preflight.model_copy(update={"warnings": ("changed",)})}
        )
        with pytest.raises(VendorUninstallConfirmationError, match="changed"):
            service.request_runtime(approved.confirmation_id, plan, changed)
    finally:
        environment.close()


def test_confirmation_state_and_binding_changes_fail_closed(tmp_path: Path) -> None:
    environment, plan, preview = _plan_preview(tmp_path)
    store = _Store()
    service = VendorUninstallConfirmationService(store)
    try:
        pending = service.request_plan(plan, preview)
        service.resolve_plan(pending.confirmation_id, True, plan, preview)
        with pytest.raises(VendorUninstallConfirmationError, match="state"):
            service.resolve_plan(pending.confirmation_id, True, plan, preview)

        second = service.request_plan(plan, preview)
        store.confirmations[second.confirmation_id] = second.model_copy(
            update={"argument_digest": "f" * 64}
        )
        with pytest.raises(VendorUninstallConfirmationError, match="bindings"):
            service.resolve_plan(second.confirmation_id, True, plan, preview)
    finally:
        environment.close()


def test_consume_rejects_stale_parent_and_consumes_valid_pair_once(tmp_path: Path) -> None:
    environment, plan, preview = _plan_preview(tmp_path)
    store = _Store()
    service = VendorUninstallConfirmationService(store)
    try:
        plan_gate = service.request_plan(plan, preview)
        plan_gate = service.resolve_plan(plan_gate.confirmation_id, True, plan, preview)
        runtime = service.request_runtime(plan_gate.confirmation_id, plan, preview)
        runtime = service.resolve_runtime(runtime.confirmation_id, True, plan, preview)

        store.confirmations[plan_gate.confirmation_id] = plan_gate.model_copy(
            update={"state": VendorUninstallConfirmationState.REJECTED}
        )
        with pytest.raises(VendorUninstallConfirmationError, match="stale"):
            service.consume_runtime(runtime.confirmation_id, plan, preview)

        store.confirmations[plan_gate.confirmation_id] = plan_gate
        consumed = service.consume_runtime(runtime.confirmation_id, plan, preview)
        assert consumed.state is VendorUninstallConfirmationState.CONSUMED
        assert store.consumed_pairs == 1

        with pytest.raises(VendorUninstallConfirmationError, match="already used"):
            service.consume_runtime(runtime.confirmation_id, plan, preview)
    finally:
        environment.close()
