"""Synthetic Stage 4D2C1 graph; no real package manager is ever invoked."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tests.fixtures.msi_uninstall import ReadySystemPlatform
from tests.fixtures.software_analysis import FakeSoftwareInventoryPlatform

from pc_manager_agent import __version__
from pc_manager_agent.app.runtime import WingetUninstallServices
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.audit.winget_uninstall import WingetUninstallAuditLogger
from pc_manager_agent.confirmation.winget_uninstall import WingetUninstallConfirmationService
from pc_manager_agent.domain.software_uninstall_analysis import (
    RawInstalledSoftwareEntry,
    RegistryHive,
    RegistryView,
    SoftwareSource,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareArchitecture, SoftwareScope
from pc_manager_agent.domain.winget_uninstall import (
    DESKTOP_APP_INSTALLER_FAMILY,
    OFFICIAL_WINGET_SOURCE_IDENTIFIER,
    NormalizedWingetPackage,
    ValidatedWingetUninstallAction,
    WingetAvailability,
    WingetAvailabilityState,
    WingetExecutableIdentity,
    WingetInventoryState,
    WingetPackageIdentity,
    WingetPackageInventory,
    WingetProcessExecutionResult,
    WingetProcessResultCategory,
)
from pc_manager_agent.orchestration.software_inventory import SoftwareInventoryService
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.orchestration.winget_execution_preflight import (
    WingetExecutionPreflightService,
)
from pc_manager_agent.orchestration.winget_inventory import (
    PackageInventoryService,
    WingetAvailabilityService,
)
from pc_manager_agent.orchestration.winget_residual_analyzer import WingetResidualAnalyzer
from pc_manager_agent.orchestration.winget_software_mapping import WingetSoftwareMapper
from pc_manager_agent.orchestration.winget_target_resolver import PackageTargetResolver
from pc_manager_agent.orchestration.winget_uninstall_execution import WingetUninstallService
from pc_manager_agent.orchestration.winget_uninstall_verifier import WingetUninstallVerifier
from pc_manager_agent.persistence.winget_uninstall import (
    WingetUninstallExecutionGuard,
    WingetUninstallRepository,
)
from pc_manager_agent.safety.software_uninstall_policy import SoftwareUninstallSafetyPolicy
from pc_manager_agent.safety.winget_capability_policy import WingetCapabilityPolicy
from pc_manager_agent.safety.winget_uninstall_policy import WingetUninstallPolicy
from pc_manager_agent.safety.winget_uninstall_preview import WingetUninstallPreviewEngine
from pc_manager_agent.safety.winget_uninstall_validator import WingetUninstallSafetyValidator
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.winget_uninstall import WingetUninstallTool


def winget_entry(
    install_location: Path,
    *,
    package_id: str = "Example.CleanApp",
    version: str = "1.0.0",
    name: str = "Example Clean App",
    publisher: str = "Example Corporation",
) -> RawInstalledSoftwareEntry:
    """Build one exact current-user software record linked to winget metadata."""
    return RawInstalledSoftwareEntry(
        raw_source_id=r"HKCU\Software\ExampleWinget",
        source=SoftwareSource.PACKAGE_MANAGER,
        display_name=name,
        display_version=version,
        publisher=publisher,
        install_location=install_location,
        scope=SoftwareScope.CURRENT_USER,
        architecture=SoftwareArchitecture.X64,
        registry_hive=RegistryHive.CURRENT_USER,
        registry_view=RegistryView.X64,
        registry_key=r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ExampleWinget",
        package_manager_id="winget",
        package_id=package_id,
    )


def package(
    package_id: str = "Example.CleanApp",
    version: str = "1.0.0",
) -> NormalizedWingetPackage:
    """Build one official-source current-user package identity."""
    identity = WingetPackageIdentity(
        package_id=package_id,
        installed_version=version,
        source_name="winget",
        source_identifier=OFFICIAL_WINGET_SOURCE_IDENTIFIER,
        scope=SoftwareScope.CURRENT_USER,
    )
    return NormalizedWingetPackage(
        identity=identity,
        package_id=package_id,
        installed_version=version,
    )


def executable_identity(alias_path: Path) -> WingetExecutableIdentity:
    """Build stable synthetic App Installer alias evidence."""
    return WingetExecutableIdentity(
        alias_path=alias_path,
        package_full_name="Microsoft.DesktopAppInstaller_1.25.0.0_x64__8wekyb3d8bbwe",
        package_family_name=DESKTOP_APP_INSTALLER_FAMILY,
        target_executable="winget.exe",
        reparse_tag=0x8000001B,
        alias_size=0,
        alias_modified_ns=1,
        alias_sha256="a" * 64,
    )


class FakeWingetAvailabilityPlatform:
    """Return mutable trusted App Installer alias evidence."""

    def __init__(self, identity: WingetExecutableIdentity) -> None:
        self.identity = identity

    def inspect(self) -> WingetAvailability:
        return WingetAvailability(
            state=WingetAvailabilityState.AVAILABLE,
            executable=self.identity,
            reason="synthetic trusted App Installer alias",
        )


class FakeWingetPackagePlatform:
    """Return mutable structured package evidence."""

    def __init__(self, packages: tuple[NormalizedWingetPackage, ...]) -> None:
        self.packages = packages
        self.state = WingetInventoryState.COMPLETE
        self.warnings: tuple[str, ...] = ()

    def inventory(
        self,
        max_items: int,
        cancellation: CancellationToken,
    ) -> WingetPackageInventory:
        del cancellation
        return WingetPackageInventory(
            state=self.state,
            packages=self.packages[:max_items],
            warnings=self.warnings,
        )


class FakeWingetUninstallPlatform:
    """Record one typed action and optionally remove both inventory identities."""

    def __init__(
        self,
        software_inventory: FakeSoftwareInventoryPlatform,
        package_inventory: FakeWingetPackagePlatform,
        *,
        remove_product: bool = True,
        category: WingetProcessResultCategory = WingetProcessResultCategory.EXITED_ZERO,
    ) -> None:
        self.software_inventory = software_inventory
        self.package_inventory = package_inventory
        self.remove_product = remove_product
        self.category = category
        self.calls: list[ValidatedWingetUninstallAction] = []

    def uninstall(
        self,
        action: ValidatedWingetUninstallAction,
        cancellation: CancellationToken,
    ) -> WingetProcessExecutionResult:
        del cancellation
        self.calls.append(action)
        if self.remove_product:
            self.software_inventory.entries = ()
            self.package_inventory.packages = ()
        launched = self.category not in {
            WingetProcessResultCategory.CANCELLED_BEFORE_LAUNCH,
            WingetProcessResultCategory.LAUNCH_FAILED,
        }
        return WingetProcessExecutionResult(
            category=self.category,
            launched=launched,
            process_id=4321 if launched else None,
            exit_code=(
                0
                if self.category is WingetProcessResultCategory.EXITED_ZERO
                else (1 if self.category is WingetProcessResultCategory.EXITED_NONZERO else None)
            ),
            cancellation_requested_before_launch=(
                self.category is WingetProcessResultCategory.CANCELLED_BEFORE_LAUNCH
            ),
            monitoring_stopped_after_launch=(
                self.category is WingetProcessResultCategory.MONITORING_STOPPED
            ),
        )


@dataclass(slots=True)
class SyntheticWingetEnvironment:
    """Resources owned by one complete synthetic winget workflow."""

    services: WingetUninstallServices
    repository: WingetUninstallRepository
    audit: AuditRepository
    software_inventory: FakeSoftwareInventoryPlatform
    package_inventory: FakeWingetPackagePlatform
    availability: FakeWingetAvailabilityPlatform
    adapter: FakeWingetUninstallPlatform
    raw: RawInstalledSoftwareEntry

    def close(self) -> None:
        """Release both SQLite engines."""
        self.repository.close()
        self.audit.close()


def build_winget_environment(
    database_path: Path,
    *,
    entry: RawInstalledSoftwareEntry | None = None,
    remove_product: bool = True,
    category: WingetProcessResultCategory = WingetProcessResultCategory.EXITED_ZERO,
) -> SyntheticWingetEnvironment:
    """Compose production orchestration entirely over synthetic local evidence."""
    install_location = database_path.parent / "winget-example-app"
    install_location.mkdir(exist_ok=True)
    raw = entry or winget_entry(install_location)
    assert raw.package_id is not None and raw.display_version is not None
    software_platform = FakeSoftwareInventoryPlatform((raw,))
    software_resolver = SoftwareTargetResolver(SoftwareInventoryService(software_platform))
    package_platform = FakeWingetPackagePlatform((package(raw.package_id, raw.display_version),))
    package_resolver = PackageTargetResolver(PackageInventoryService(package_platform))
    alias = executable_identity(database_path.parent / "WindowsApps" / "winget.exe")
    availability_platform = FakeWingetAvailabilityPlatform(alias)
    repository = WingetUninstallRepository(database_path)
    repository.initialize()
    adapter = FakeWingetUninstallPlatform(
        software_platform,
        package_platform,
        remove_product=remove_product,
        category=category,
    )
    registry = ToolRegistry(write_guard=WingetUninstallExecutionGuard(repository))
    registry.register(WingetUninstallTool(adapter))
    audit = AuditRepository(database_path)
    audit.initialize()
    service = WingetUninstallService(
        package_resolver=package_resolver,
        software_resolver=software_resolver,
        mapper=WingetSoftwareMapper(),
        availability=WingetAvailabilityService(availability_platform),
        capability=WingetCapabilityPolicy(),
        analysis_policy=SoftwareUninstallSafetyPolicy(
            Path("D:/SyntheticAgent"),
            Path("C:/Windows"),
        ),
        execution_policy=WingetUninstallPolicy(),
        preflight=WingetExecutionPreflightService(ReadySystemPlatform()),
        preview_engine=WingetUninstallPreviewEngine(),
        validator=WingetUninstallSafetyValidator(),
        repository=repository,
        confirmations=WingetUninstallConfirmationService(repository),
        registry=registry,
        verifier=WingetUninstallVerifier(package_resolver, software_resolver),
        residual=WingetResidualAnalyzer(),
        audit=WingetUninstallAuditLogger(audit, app_version=__version__, git_commit="test"),
        process_is_elevated=lambda: False,
    )
    services = WingetUninstallServices(
        registry=registry,
        software_resolver=software_resolver,
        package_resolver=package_resolver,
        service=service,
    )
    return SyntheticWingetEnvironment(
        services=services,
        repository=repository,
        audit=audit,
        software_inventory=software_platform,
        package_inventory=package_platform,
        availability=availability_platform,
        adapter=adapter,
        raw=raw,
    )
