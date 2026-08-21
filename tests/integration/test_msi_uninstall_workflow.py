from __future__ import annotations

from pathlib import Path

import pytest
from tests.fixtures.msi_uninstall import build_msi_environment

from pc_manager_agent.confirmation.software_uninstall_execution import (
    MsiUninstallConfirmationError,
)
from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiInstallerResultCategory,
    MsiUninstallTransactionState,
    MsiVerificationState,
)


def test_full_workflow_requires_both_confirmations_and_verifies_removal(tmp_path: Path) -> None:
    environment = build_msi_environment(tmp_path / "state.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载软件 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.plan is not None
        assert prepared.preview is not None
        assert prepared.plan_confirmation is not None
        plan = prepared.plan
        preview = prepared.preview
        plan_confirmation = environment.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            plan,
            preview,
        )
        runtime = environment.services.service.prepare_runtime_confirmation(
            plan_confirmation.confirmation_id,
            plan,
        )
        environment.services.service.resolve_runtime_confirmation(
            runtime.confirmation.confirmation_id,
            True,
            plan,
            runtime.preview,
        )
        report = environment.services.service.execute(
            runtime.confirmation.confirmation_id,
            plan,
            runtime.preview,
        )
        assert report.installer.category is MsiInstallerResultCategory.SUCCESS
        assert report.verification.state is MsiVerificationState.VERIFIED_REMOVED
        assert environment.repository.state(plan.transaction_id) is (
            MsiUninstallTransactionState.VERIFIED_REMOVED
        )
        assert len(environment.adapter.calls) == 1
        assert report.residual.deletion_performed is False
    finally:
        environment.close()


def test_confirmation_replay_does_not_launch_second_uninstall(tmp_path: Path) -> None:
    environment = build_msi_environment(tmp_path / "state.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载软件 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        plan_confirmation = environment.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        runtime = environment.services.service.prepare_runtime_confirmation(
            plan_confirmation.confirmation_id,
            prepared.plan,
        )
        environment.services.service.resolve_runtime_confirmation(
            runtime.confirmation.confirmation_id,
            True,
            prepared.plan,
            runtime.preview,
        )
        environment.services.service.execute(
            runtime.confirmation.confirmation_id,
            prepared.plan,
            runtime.preview,
        )
        with pytest.raises(MsiUninstallConfirmationError):
            environment.services.service.execute(
                runtime.confirmation.confirmation_id,
                prepared.plan,
                runtime.preview,
            )
        assert len(environment.adapter.calls) == 1
    finally:
        environment.close()


def test_success_exit_with_product_still_present_is_not_verified_success(tmp_path: Path) -> None:
    environment = build_msi_environment(
        tmp_path / "state.sqlite3",
        remove_product=False,
    )
    try:
        prepared = environment.services.service.prepare(
            "卸载软件 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        plan_confirmation = environment.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        runtime = environment.services.service.prepare_runtime_confirmation(
            plan_confirmation.confirmation_id,
            prepared.plan,
        )
        environment.services.service.resolve_runtime_confirmation(
            runtime.confirmation.confirmation_id,
            True,
            prepared.plan,
            runtime.preview,
        )
        report = environment.services.service.execute(
            runtime.confirmation.confirmation_id,
            prepared.plan,
            runtime.preview,
        )
        assert report.verification.state is MsiVerificationState.COMPLETED_UNVERIFIED
    finally:
        environment.close()


def test_partial_post_uninstall_inventory_cannot_prove_removal(tmp_path: Path) -> None:
    environment = build_msi_environment(tmp_path / "state.sqlite3")
    environment.adapter.make_inventory_partial_after = True
    try:
        prepared = environment.services.service.prepare(
            "卸载软件 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        plan_confirmation = environment.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        runtime = environment.services.service.prepare_runtime_confirmation(
            plan_confirmation.confirmation_id,
            prepared.plan,
        )
        environment.services.service.resolve_runtime_confirmation(
            runtime.confirmation.confirmation_id,
            True,
            prepared.plan,
            runtime.preview,
        )
        report = environment.services.service.execute(
            runtime.confirmation.confirmation_id,
            prepared.plan,
            runtime.preview,
        )
        assert report.verification.state is MsiVerificationState.COMPLETED_UNVERIFIED
        assert not report.verification.inventory_refreshed
    finally:
        environment.close()


def test_crash_recovery_marks_active_transaction_interrupted(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    environment = build_msi_environment(database)
    prepared = environment.services.service.prepare(
        "卸载软件 Example App",
        SoftwareTargetQuery(display_name="Example App"),
    )
    assert prepared.plan and prepared.preview and prepared.plan_confirmation
    plan_confirmation = environment.services.service.resolve_plan_confirmation(
        prepared.plan_confirmation.confirmation_id,
        True,
        prepared.plan,
        prepared.preview,
    )
    runtime = environment.services.service.prepare_runtime_confirmation(
        plan_confirmation.confirmation_id,
        prepared.plan,
    )
    runtime_confirmation = environment.services.service.resolve_runtime_confirmation(
        runtime.confirmation.confirmation_id,
        True,
        prepared.plan,
        runtime.preview,
    )
    environment.repository.consume_confirmation_pair(
        plan_confirmation,
        runtime_confirmation,
    )
    environment.close()

    restarted = build_msi_environment(database)
    try:
        assert restarted.repository.state(prepared.plan.transaction_id) is (
            MsiUninstallTransactionState.INTERRUPTED
        )
        assert restarted.adapter.calls == []
    finally:
        restarted.close()


def test_long_running_installer_stays_waiting_without_premature_verification(
    tmp_path: Path,
) -> None:
    environment = build_msi_environment(
        tmp_path / "state.sqlite3",
        category=MsiInstallerResultCategory.MONITORING_DETACHED,
        remove_product=False,
    )
    try:
        prepared = environment.services.service.prepare(
            "卸载软件 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        plan_confirmation = environment.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        runtime = environment.services.service.prepare_runtime_confirmation(
            plan_confirmation.confirmation_id,
            prepared.plan,
        )
        environment.services.service.resolve_runtime_confirmation(
            runtime.confirmation.confirmation_id,
            True,
            prepared.plan,
            runtime.preview,
        )
        report = environment.services.service.execute(
            runtime.confirmation.confirmation_id,
            prepared.plan,
            runtime.preview,
        )
        assert report.verification.state is MsiVerificationState.INTERRUPTED
        assert not report.verification.inventory_refreshed
        assert environment.repository.state(prepared.plan.transaction_id) is (
            MsiUninstallTransactionState.WAITING
        )
        assert environment.software_inventory.calls == 3
    finally:
        environment.close()
