"""Synthetic Stage 4D2A service graph; no real installer is ever invoked."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tests.fixtures.software_analysis import FakeSoftwareInventoryPlatform, msi_entry
from tests.fixtures.system_diagnostics import FakeSystemPlatform

from pc_manager_agent import __version__
from pc_manager_agent.app.runtime import MsiUninstallServices
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.audit.software_uninstall_execution import MsiUninstallAuditLogger
from pc_manager_agent.confirmation.software_uninstall_execution import (
    MsiUninstallConfirmationService,
)
from pc_manager_agent.domain.software_uninstall_analysis import RawInstalledSoftwareEntry
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiInstallContext,
    MsiInstallerExecutionResult,
    MsiInstallerResultCategory,
    MsiProductRegistration,
    ValidatedMsiProduct,
)
from pc_manager_agent.domain.system_diagnostics import ProcessCollection, ServiceSnapshot
from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
from pc_manager_agent.orchestration.software_execution_preflight import (
    SoftwareExecutionPreflight,
)
from pc_manager_agent.orchestration.software_inventory import SoftwareInventoryService
from pc_manager_agent.orchestration.software_msi_validation import MsiProductValidator
from pc_manager_agent.orchestration.software_residual_analyzer import SoftwareResidualAnalyzer
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.orchestration.software_uninstall_execution import MsiUninstallService
from pc_manager_agent.orchestration.software_uninstall_verifier import MsiUninstallVerifier
from pc_manager_agent.persistence.software_uninstall_execution import (
    MsiUninstallExecutionGuard,
    MsiUninstallRepository,
)
from pc_manager_agent.safety.software_uninstall_execution_policy import (
    SoftwareUninstallExecutionPolicy,
)
from pc_manager_agent.safety.software_uninstall_execution_preview import (
    MsiUninstallPreviewEngine,
)
from pc_manager_agent.safety.software_uninstall_execution_validator import (
    MsiUninstallSafetyValidator,
)
from pc_manager_agent.safety.software_uninstall_policy import SoftwareUninstallSafetyPolicy
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.software_uninstall import MsiUninstallTool


class ReadySystemPlatform(FakeSystemPlatform):
    """Return complete process/service evidence with no path-related objects."""

    def collect_processes(
        self,
        interval_seconds: float,
        max_processes: int,
        cancellation: CancellationToken,
    ) -> tuple[ProcessCollection, tuple[str, ...]]:
        return (
            ProcessCollection(
                processes=(),
                groups=(),
                complete_count=0,
                partial_count=0,
                skipped_count=0,
            ),
            (),
        )

    def collect_services(
        self, max_items: int
    ) -> tuple[tuple[ServiceSnapshot, ...], tuple[str, ...], bool]:
        return (), (), False


class FakeMsiInventory:
    """Mutable exact registration returned by the Windows Installer test boundary."""

    def __init__(self, registration: MsiProductRegistration) -> None:
        self.registration = registration
        self.calls: list[str] = []

    def registrations(self, product_code: str) -> tuple[MsiProductRegistration, ...]:
        self.calls.append(product_code)
        return (self.registration,) if self.registration.installed else ()


class FakeMsiUninstallPlatform:
    """Record one typed product and optionally remove synthetic inventory evidence."""

    def __init__(
        self,
        inventory: FakeSoftwareInventoryPlatform,
        msi_inventory: FakeMsiInventory,
        *,
        category: MsiInstallerResultCategory = MsiInstallerResultCategory.SUCCESS,
        remove_product: bool = True,
    ) -> None:
        self.inventory = inventory
        self.msi_inventory = msi_inventory
        self.category = category
        self.remove_product = remove_product
        self.make_inventory_partial_after = False
        self.calls: list[ValidatedMsiProduct] = []

    def uninstall(
        self,
        product: ValidatedMsiProduct,
        cancellation: CancellationToken,
    ) -> MsiInstallerExecutionResult:
        self.calls.append(product)
        if self.remove_product:
            self.inventory.entries = ()
            self.inventory.truncated = self.make_inventory_partial_after
            self.msi_inventory.registration = self.msi_inventory.registration.model_copy(
                update={"installed": False}
            )
        exit_code = {
            MsiInstallerResultCategory.SUCCESS: 0,
            MsiInstallerResultCategory.SUCCESS_REBOOT_REQUIRED: 3010,
            MsiInstallerResultCategory.USER_CANCELLED: 1602,
            MsiInstallerResultCategory.PRIVILEGE_REQUIRED: 5,
            MsiInstallerResultCategory.MONITORING_DETACHED: None,
        }.get(self.category, 1603)
        return MsiInstallerExecutionResult(
            category=self.category,
            exit_code=exit_code,
            launched=True,
        )


@dataclass(slots=True)
class SyntheticMsiEnvironment:
    """Resources owned by one full workflow test."""

    services: MsiUninstallServices
    repository: MsiUninstallRepository
    audit: AuditRepository
    software_inventory: FakeSoftwareInventoryPlatform
    msi_inventory: FakeMsiInventory
    adapter: FakeMsiUninstallPlatform

    def close(self) -> None:
        """Release both SQLite engines."""
        self.repository.close()
        self.audit.close()


def build_msi_environment(
    database_path: Path,
    *,
    entry: RawInstalledSoftwareEntry | None = None,
    category: MsiInstallerResultCategory = MsiInstallerResultCategory.SUCCESS,
    remove_product: bool = True,
) -> SyntheticMsiEnvironment:
    """Compose the production orchestrator entirely over synthetic local evidence."""
    raw = entry or msi_entry(install_location=database_path.parent / "missing-app")
    software_inventory = FakeSoftwareInventoryPlatform((raw,))
    inventory = SoftwareInventoryService(software_inventory)
    resolver = SoftwareTargetResolver(inventory)
    product_code = raw.product_code
    assert product_code is not None
    registration = MsiProductRegistration(
        product_code=product_code,
        context=MsiInstallContext.USER_UNMANAGED,
        product_name=raw.display_name,
        version=raw.display_version,
        publisher=raw.publisher,
        install_location=raw.install_location,
        installed=True,
    )
    msi_inventory = FakeMsiInventory(registration)
    adapter = FakeMsiUninstallPlatform(
        software_inventory,
        msi_inventory,
        category=category,
        remove_product=remove_product,
    )
    repository = MsiUninstallRepository(database_path)
    repository.initialize()
    registry = ToolRegistry(write_guard=MsiUninstallExecutionGuard(repository))
    registry.register(MsiUninstallTool(adapter))
    audit = AuditRepository(database_path)
    audit.initialize()
    service = MsiUninstallService(
        resolver,
        UninstallCapabilityResolver(),
        MsiProductValidator(msi_inventory),
        SoftwareUninstallSafetyPolicy(Path("D:/SyntheticAgent"), Path("C:/Windows")),
        SoftwareUninstallExecutionPolicy(),
        SoftwareExecutionPreflight(ReadySystemPlatform()),
        MsiUninstallPreviewEngine(),
        MsiUninstallSafetyValidator(registry),
        MsiUninstallConfirmationService(repository),
        repository,
        registry,
        MsiUninstallVerifier(resolver, msi_inventory),
        SoftwareResidualAnalyzer(),
        MsiUninstallAuditLogger(audit, app_version=__version__, git_commit="test"),
        process_is_elevated=lambda: False,
    )
    return SyntheticMsiEnvironment(
        services=MsiUninstallServices(registry=registry, resolver=resolver, service=service),
        repository=repository,
        audit=audit,
        software_inventory=software_inventory,
        msi_inventory=msi_inventory,
        adapter=adapter,
    )
