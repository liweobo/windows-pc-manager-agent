"""Expiry, binding, rejection, and replay tests for winget confirmations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.confirmation.winget_uninstall import (
    WingetUninstallConfirmationError,
    WingetUninstallConfirmationService,
    WingetUninstallConfirmationState,
)
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.domain.winget_uninstall import WingetUninstallTransactionState
from tests.fixtures.winget_uninstall import build_winget_environment


def test_confirmation_ttls_must_be_positive(tmp_path: Path) -> None:
    environment = build_winget_environment(tmp_path / "ttl.sqlite3")
    try:
        with pytest.raises(ValueError, match="positive"):
            WingetUninstallConfirmationService(environment.repository, plan_ttl_seconds=0)
    finally:
        environment.close()


def test_rejected_plan_is_durable_and_cancels_transaction(tmp_path: Path) -> None:
    environment = build_winget_environment(tmp_path / "rejected.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example Clean App",
            SoftwareTargetQuery(display_name="Example Clean App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        rejected = environment.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            False,
            prepared.plan,
            prepared.preview,
        )
        assert rejected.state is WingetUninstallConfirmationState.REJECTED
        assert environment.repository.state(prepared.plan.transaction_id) is (
            WingetUninstallTransactionState.CANCELLED
        )
        confirmations = WingetUninstallConfirmationService(environment.repository)
        with pytest.raises(WingetUninstallConfirmationError, match="approved"):
            confirmations.request_runtime(
                rejected.confirmation_id,
                prepared.plan,
                prepared.preview,
            )
    finally:
        environment.close()


def test_expired_plan_is_marked_expired_and_cannot_be_approved(tmp_path: Path) -> None:
    environment = build_winget_environment(tmp_path / "expired.sqlite3")
    try:
        clock = [datetime.now(UTC)]
        service = WingetUninstallConfirmationService(
            environment.repository,
            plan_ttl_seconds=1,
            now=lambda: clock[0],
        )
        environment.services.service._confirmations = service
        prepared = environment.services.service.prepare(
            "卸载 Example Clean App",
            SoftwareTargetQuery(display_name="Example Clean App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        clock[0] += timedelta(seconds=2)
        with pytest.raises(WingetUninstallConfirmationError, match="expired"):
            service.resolve_plan(
                prepared.plan_confirmation.confirmation_id,
                True,
                prepared.plan,
                prepared.preview,
            )
        stored = environment.repository.get_confirmation(prepared.plan_confirmation.confirmation_id)
        assert stored.state is WingetUninstallConfirmationState.EXPIRED
    finally:
        environment.close()


def test_changed_preview_binding_and_unapproved_runtime_are_rejected(tmp_path: Path) -> None:
    environment = build_winget_environment(tmp_path / "binding.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example Clean App",
            SoftwareTargetQuery(display_name="Example Clean App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        changed = prepared.preview.model_copy(update={"preview_id": uuid4()})
        with pytest.raises(WingetUninstallConfirmationError, match="bindings changed"):
            environment.services.service.resolve_plan_confirmation(
                prepared.plan_confirmation.confirmation_id,
                True,
                prepared.plan,
                changed,
            )

        approved = environment.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        with pytest.raises(WingetUninstallConfirmationError, match="tier or state"):
            environment.services.service.resolve_plan_confirmation(
                prepared.plan_confirmation.confirmation_id,
                True,
                prepared.plan,
                prepared.preview,
            )

        changed_evidence = prepared.preview.model_copy(
            update={
                "preflight": prepared.preview.preflight.model_copy(
                    update={"warnings": ("changed",)}
                )
            }
        )
        with pytest.raises(WingetUninstallConfirmationError, match="evidence changed"):
            WingetUninstallConfirmationService(environment.repository).request_runtime(
                approved.confirmation_id,
                prepared.plan,
                changed_evidence,
            )
        immediate = environment.services.service.prepare_runtime_confirmation(
            approved.confirmation_id,
            prepared.plan,
        )
        with pytest.raises(WingetUninstallConfirmationError, match="absent or used"):
            environment.services.service.execute(
                immediate.confirmation.confirmation_id,
                prepared.plan,
                immediate.preview,
            )
        assert environment.adapter.calls == []
    finally:
        environment.close()


def test_stale_preview_cannot_create_a_plan_confirmation(tmp_path: Path) -> None:
    environment = build_winget_environment(tmp_path / "stale.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example Clean App",
            SoftwareTargetQuery(display_name="Example Clean App"),
        )
        assert prepared.plan and prepared.preview
        stale = prepared.preview.model_copy(
            update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
        )
        with pytest.raises(WingetUninstallConfirmationError, match="blocked or stale"):
            WingetUninstallConfirmationService(environment.repository).request_plan(
                prepared.plan,
                stale,
            )
    finally:
        environment.close()
