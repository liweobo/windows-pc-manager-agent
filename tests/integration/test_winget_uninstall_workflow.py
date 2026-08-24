"""Integration coverage for the full synthetic Stage 4D2C1 workflow."""

from __future__ import annotations

from pathlib import Path

from tests.fixtures.winget_uninstall import build_winget_environment

from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.domain.winget_uninstall import (
    WingetProcessResultCategory,
    WingetUninstallTransactionState,
    WingetVerificationState,
)


def test_full_workflow_requires_both_confirmations_and_dual_verification(
    tmp_path: Path,
) -> None:
    environment = build_winget_environment(tmp_path / "state.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example Clean App",
            SoftwareTargetQuery(display_name="Example Clean App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        plan_gate = environment.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        immediate = environment.services.service.prepare_runtime_confirmation(
            plan_gate.confirmation_id,
            prepared.plan,
        )
        environment.services.service.resolve_runtime_confirmation(
            immediate.confirmation.confirmation_id,
            True,
            prepared.plan,
            immediate.preview,
        )
        report = environment.services.service.execute(
            immediate.confirmation.confirmation_id,
            prepared.plan,
            immediate.preview,
        )
        assert len(environment.adapter.calls) == 1
        assert report.verification.state is WingetVerificationState.VERIFIED_REMOVED
        assert report.verification.package_inventory_refreshed
        assert report.verification.software_inventory_refreshed
        assert not report.residual.deletion_performed
        events = environment.audit.list_recent(20)
        assert any(row.event_type == "software.winget_uninstall.started" for row in events)
        assert any(row.event_type == "software.winget_uninstall.completed" for row in events)
    finally:
        environment.close()


def test_cancelled_before_launch_and_launch_failure_never_claim_removal(tmp_path: Path) -> None:
    for category, expected_state, expected_verification in (
        (
            WingetProcessResultCategory.CANCELLED_BEFORE_LAUNCH,
            WingetUninstallTransactionState.CANCELLED,
            WingetVerificationState.INTERRUPTED,
        ),
        (
            WingetProcessResultCategory.LAUNCH_FAILED,
            WingetUninstallTransactionState.FAILED,
            WingetVerificationState.FAILED,
        ),
    ):
        environment = build_winget_environment(
            tmp_path / f"{category.value}.sqlite3",
            category=category,
            remove_product=False,
        )
        try:
            prepared = environment.services.service.prepare(
                "卸载 Example Clean App",
                SoftwareTargetQuery(display_name="Example Clean App"),
            )
            assert prepared.plan and prepared.preview and prepared.plan_confirmation
            parent = environment.services.service.resolve_plan_confirmation(
                prepared.plan_confirmation.confirmation_id,
                True,
                prepared.plan,
                prepared.preview,
            )
            immediate = environment.services.service.prepare_runtime_confirmation(
                parent.confirmation_id,
                prepared.plan,
            )
            environment.services.service.resolve_runtime_confirmation(
                immediate.confirmation.confirmation_id,
                True,
                prepared.plan,
                immediate.preview,
            )
            report = environment.services.service.execute(
                immediate.confirmation.confirmation_id,
                prepared.plan,
                immediate.preview,
            )
            assert report.verification.state is expected_verification
            assert not report.process.launched
            assert environment.repository.state(prepared.plan.transaction_id) is expected_state
            assert environment.package_inventory.packages
            assert environment.software_inventory.entries
        finally:
            environment.close()
