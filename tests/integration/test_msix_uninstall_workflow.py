"""Integration coverage for the durable Stage 4D2C2 workflow."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.test_msix_uninstall import plan_preview

from pc_manager_agent.audit.msix_uninstall import MsixUninstallAuditLogger
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.confirmation.msix_uninstall import MsixConfirmationService
from pc_manager_agent.domain.msix_uninstall import (
    MsixDependencySnapshot,
    MsixDependencyState,
    MsixDeploymentResult,
    MsixFamilyIdentity,
    MsixInstanceIdentity,
    MsixInventoryState,
    MsixPackageIdentity,
    MsixPackageInventory,
    MsixPackageType,
    MsixRemovalResultCategory,
    MsixScope,
    MsixTargetQuery,
    MsixTransactionState,
    MsixVerificationState,
    NormalizedMsixPackage,
    ValidatedMsixRemovalAction,
)
from pc_manager_agent.orchestration.msix_uninstall import ready_msix_preflight
from pc_manager_agent.orchestration.msix_uninstall_execution import MsixUninstallService
from pc_manager_agent.persistence.msix_uninstall import (
    MsixUninstallExecutionGuard,
    MsixUninstallRepository,
    MsixUninstallStoreError,
)
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.msix_uninstall import MsixUninstallTool


def synthetic_package() -> NormalizedMsixPackage:
    """Return a synthetic ordinary current-user Store app."""
    return NormalizedMsixPackage(
        identity=MsixPackageIdentity(
            family=MsixFamilyIdentity(
                family_name="Example.App_publisher",
                name="Example.App",
                publisher_id="publisher",
            ),
            instance=MsixInstanceIdentity(
                full_name="Example.App_1.0.0.0_x64__publisher",
                version="1.0.0.0",
                architecture="x64",
            ),
            scope=MsixScope.CURRENT_USER,
            package_type=MsixPackageType.USER_MSIX_APP,
            current_user_registered=True,
            is_framework=False,
            is_resource=False,
            is_bundle=False,
            is_optional=False,
            is_development_mode=False,
            is_stub=False,
            signature_kind="store",
            status_ok=True,
        ),
        display_name="Example App",
        installed_path=None,
    )


class FakeMsixPlatform:
    """Structured fake that simulates Windows registration removal only."""

    def __init__(self) -> None:
        self.package = synthetic_package()
        self.present = True
        self.remove_calls = 0

    def inventory_current_user(
        self, max_items: int, cancellation: CancellationToken
    ) -> MsixPackageInventory:
        """Return the synthetic current-user registration while present."""
        assert max_items > 0
        return MsixPackageInventory(
            state=MsixInventoryState.COMPLETE,
            packages=(self.package,) if self.present else (),
        )

    def dependency_snapshot(self, identity: MsixPackageIdentity) -> MsixDependencySnapshot:
        """Return complete no-risk relationship evidence."""
        return MsixDependencySnapshot(
            state=MsixDependencyState.COMPLETE,
            target_identity_digest=identity.canonical_digest(),
            orphan_dependency_risk=False,
        )

    def remove_current_user(
        self, action: ValidatedMsixRemovalAction, cancellation: CancellationToken
    ) -> MsixDeploymentResult:
        """Remove the one synthetic registration without touching filesystem data."""
        assert action.preserve_roamable_application_data is True
        assert not cancellation.is_cancelled
        self.remove_calls += 1
        self.present = False
        return MsixDeploymentResult(
            category=MsixRemovalResultCategory.REMOVAL_COMPLETED,
            dispatched=True,
        )


def test_full_msix_workflow_is_twice_confirmed_and_freshly_verified(tmp_path: Path) -> None:
    """One exact request passes durable gates, dispatches once, and verifies inventory absence."""
    database = tmp_path / "state.sqlite3"
    repository = MsixUninstallRepository(database)
    repository.initialize()
    audit_repository = AuditRepository(database)
    audit_repository.initialize()
    platform = FakeMsixPlatform()
    registry = ToolRegistry(MsixUninstallExecutionGuard(repository))
    registry.register(MsixUninstallTool(platform))
    service = MsixUninstallService(
        platform=platform,
        repository=repository,
        confirmations=MsixConfirmationService(repository),
        registry=registry,
        audit=MsixUninstallAuditLogger(
            audit_repository,
            app_version="test",
            git_commit=None,
        ),
        preflight=lambda _package, active: ready_msix_preflight(another_uninstall_active=active),
        process_is_elevated=lambda: False,
    )
    prepared = service.prepare(
        "remove example app",
        MsixTargetQuery(full_name=platform.package.identity.instance.full_name),
    )
    assert prepared.plan and prepared.preview and prepared.plan_confirmation
    service.resolve_confirmation(
        prepared.plan_confirmation.confirmation_id,
        True,
        prepared.plan,
        prepared.preview,
    )
    runtime = service.prepare_runtime_confirmation(
        prepared.plan_confirmation.confirmation_id,
        prepared.plan,
        prepared.preview,
    )
    service.resolve_confirmation(
        runtime.confirmation.confirmation_id,
        True,
        prepared.plan,
        runtime.preview,
    )
    result = service.execute(
        runtime.confirmation.confirmation_id,
        prepared.plan,
        runtime.preview,
    )
    assert platform.remove_calls == 1
    assert result.verification.state is MsixVerificationState.VERIFIED_REMOVED
    assert result.residual.user_data_deleted_by_agent is False
    assert len(audit_repository.list_recent()) == 5
    repository.close()
    audit_repository.close()


def test_msix_restart_marks_active_transaction_interrupted_without_redispatch(
    tmp_path: Path,
) -> None:
    """Crash recovery expires authorization and never calls the removal adapter."""
    database = tmp_path / "restart.sqlite3"
    plan, preview = plan_preview()
    first = MsixUninstallRepository(database)
    first.initialize()
    first.create(plan, preview)
    first.close()
    recovered = MsixUninstallRepository(database)
    interrupted = recovered.initialize()
    assert interrupted == (plan.transaction_id,)
    assert recovered.state(plan.transaction_id) is MsixTransactionState.INTERRUPTED
    recovered.close()


def test_msix_repository_blocks_second_active_uninstall(tmp_path: Path) -> None:
    """Global software-uninstall slot rejects double-click/concurrent transactions."""
    database = tmp_path / "concurrency.sqlite3"
    first_plan, first_preview = plan_preview()
    second_plan, second_preview = plan_preview()
    repository = MsixUninstallRepository(database)
    repository.initialize()
    repository.create(first_plan, first_preview)
    with pytest.raises(MsixUninstallStoreError, match="Another"):
        repository.create(second_plan, second_preview)
    repository.close()
