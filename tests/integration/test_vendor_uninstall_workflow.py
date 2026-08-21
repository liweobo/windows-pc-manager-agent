"""End-to-end synthetic Stage 4D2B workflow tests."""

from __future__ import annotations

from pathlib import Path

from tests.fixtures.vendor_uninstall import build_vendor_environment

from pc_manager_agent.domain.software_uninstall_analysis import SoftwareTargetQuery
from pc_manager_agent.domain.vendor_uninstall import (
    VendorProcessResultCategory,
    VendorUninstallTransactionState,
    VendorVerificationState,
)
from pc_manager_agent.persistence.vendor_uninstall import VendorUninstallRepository


def test_full_vendor_flow_consumes_two_confirmations_and_verifies_removal(
    tmp_path: Path,
) -> None:
    environment = build_vendor_environment(tmp_path / "state.sqlite3")
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example App",
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

        assert report.verification.state is VendorVerificationState.VERIFIED_REMOVED
        assert not report.residual.deletion_performed
        assert environment.repository.state(prepared.plan.transaction_id) is (
            VendorUninstallTransactionState.VERIFIED_REMOVED
        )
        assert len(environment.adapter.calls) == 1
        assert environment.adapter.calls[0].vendor_identity.arguments == ("/remove",)
    finally:
        environment.close()


def test_process_exit_zero_is_not_success_when_inventory_still_contains_target(
    tmp_path: Path,
) -> None:
    environment = build_vendor_environment(
        tmp_path / "state.sqlite3",
        remove_product=False,
    )
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        parent = environment.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        runtime = environment.services.service.prepare_runtime_confirmation(
            parent.confirmation_id,
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
        assert report.verification.state is VendorVerificationState.FAILED
    finally:
        environment.close()


def test_nonzero_process_result_still_uses_fresh_inventory_truth(tmp_path: Path) -> None:
    environment = build_vendor_environment(
        tmp_path / "state.sqlite3",
        category=VendorProcessResultCategory.PROCESS_EXITED_NONZERO,
    )
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        parent = environment.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        runtime = environment.services.service.prepare_runtime_confirmation(
            parent.confirmation_id,
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
        assert report.verification.state is (
            VendorVerificationState.REMOVED_WITH_UNEXPECTED_PROCESS_RESULT
        )
        assert environment.repository.state(prepared.plan.transaction_id) is (
            VendorUninstallTransactionState.COMPLETED_UNVERIFIED
        )
    finally:
        environment.close()


def test_long_running_vendor_ui_remains_active_and_is_not_force_killed(tmp_path: Path) -> None:
    environment = build_vendor_environment(
        tmp_path / "state.sqlite3",
        remove_product=False,
        category=VendorProcessResultCategory.MONITORING_DETACHED,
    )
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example App",
            SoftwareTargetQuery(display_name="Example App"),
        )
        assert prepared.plan and prepared.preview and prepared.plan_confirmation
        parent = environment.services.service.resolve_plan_confirmation(
            prepared.plan_confirmation.confirmation_id,
            True,
            prepared.plan,
            prepared.preview,
        )
        runtime = environment.services.service.prepare_runtime_confirmation(
            parent.confirmation_id,
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

        assert report.verification.state is VendorVerificationState.INTERRUPTED
        assert environment.repository.state(prepared.plan.transaction_id) is (
            VendorUninstallTransactionState.MONITORING
        )
        assert environment.repository.has_active_uninstall()
        assert len(environment.adapter.calls) == 1
    finally:
        environment.close()


def test_restart_marks_monitoring_transaction_interrupted_without_redispatch(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"
    environment = build_vendor_environment(database)
    reopened: VendorUninstallRepository | None = None
    try:
        prepared = environment.services.service.prepare(
            "卸载 Example App",
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
        environment.repository.transition(
            prepared.plan.transaction_id,
            VendorUninstallTransactionState.EXECUTING,
        )
        environment.repository.transition(
            prepared.plan.transaction_id,
            VendorUninstallTransactionState.MONITORING,
        )
        environment.repository.close()

        reopened = VendorUninstallRepository(database)
        interrupted = reopened.initialize()

        assert interrupted == (prepared.plan.transaction_id,)
        assert reopened.state(prepared.plan.transaction_id) is (
            VendorUninstallTransactionState.INTERRUPTED
        )
        assert environment.adapter.calls == []
    finally:
        if reopened is not None:
            reopened.close()
        environment.close()
