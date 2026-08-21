from __future__ import annotations

from pathlib import Path

from tests.fixtures.system_diagnostics import FakeSystemPlatform

from pc_manager_agent import __version__
from pc_manager_agent.app.runtime import SoftwareAnalysisServices
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.audit.software_uninstall_analysis import (
    SoftwareUninstallAnalysisAuditLogger,
)
from pc_manager_agent.confirmation.software_uninstall_analysis import (
    SoftwareAnalysisConfirmationService,
)
from pc_manager_agent.domain.software_uninstall_analysis import (
    RawInstalledSoftwareEntry,
    RegistryHive,
    RegistryView,
    SoftwareSource,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareArchitecture, SoftwareScope
from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
from pc_manager_agent.orchestration.software_impact_analyzer import SoftwareImpactAnalyzer
from pc_manager_agent.orchestration.software_inventory import SoftwareInventoryService
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.orchestration.software_uninstall_analysis import (
    SoftwareUninstallAnalysisPlanCompiler,
    SoftwareUninstallAnalysisService,
)
from pc_manager_agent.platform_support.base import CancellationSignal
from pc_manager_agent.safety.software_uninstall_policy import SoftwareUninstallSafetyPolicy
from pc_manager_agent.safety.software_uninstall_preview import SoftwareUninstallPreviewEngine
from pc_manager_agent.safety.software_uninstall_validator import (
    SoftwareUninstallSafetyValidator,
)
from pc_manager_agent.safety.software_zero_execution import SoftwareZeroExecutionGuard
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.software_analysis import (
    SoftwareInspectTool,
    SoftwareInventoryTool,
    SoftwareResolveTool,
    SoftwareUninstallCapabilityTool,
    SoftwareUninstallPreviewTool,
)


class FakeSoftwareInventoryPlatform:
    def __init__(
        self,
        entries: tuple[RawInstalledSoftwareEntry, ...],
        *,
        warnings: tuple[str, ...] = (),
        truncated: bool = False,
    ) -> None:
        self.entries = entries
        self.warnings = warnings
        self.truncated = truncated
        self.calls = 0

    def collect_raw(
        self, max_items: int, cancellation: CancellationSignal
    ) -> tuple[tuple[RawInstalledSoftwareEntry, ...], tuple[str, ...], bool]:
        self.calls += 1
        cancelled = cancellation.cancellation_requested()
        if cancelled:
            return (), ("cancelled",), True
        return (
            self.entries[:max_items],
            self.warnings,
            self.truncated or len(self.entries) > max_items,
        )


def msi_entry(
    *,
    name: str = "Example App",
    version: str = "1.0",
    publisher: str = "Example Publisher",
    product_code: str = "{12345678-1234-1234-1234-1234567890AB}",
    architecture: SoftwareArchitecture = SoftwareArchitecture.X64,
    install_location: Path = Path("C:/Apps"),
) -> RawInstalledSoftwareEntry:
    key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{product_code}"
    return RawInstalledSoftwareEntry(
        raw_source_id=f"HKEY_CURRENT_USER|x64|{key}|{architecture.value}",
        source=SoftwareSource.MSI,
        display_name=name,
        display_version=version,
        publisher=publisher,
        install_location=install_location,
        install_date="20260801",
        estimated_size_bytes=1024,
        scope=SoftwareScope.CURRENT_USER,
        architecture=architecture,
        registry_hive=RegistryHive.CURRENT_USER,
        registry_view=RegistryView.X64,
        registry_key=key,
        product_code=product_code,
        windows_installer=True,
        system_component=False,
        uninstall_string=f"MsiExec.exe /X {product_code}",
    )


def vendor_entry(executable: Path) -> RawInstalledSoftwareEntry:
    key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\VendorApp"
    return RawInstalledSoftwareEntry(
        raw_source_id=f"HKEY_CURRENT_USER|x64|{key}",
        source=SoftwareSource.VENDOR,
        display_name="Vendor App",
        display_version="2.0",
        publisher="Vendor Ltd",
        install_location=executable.parent,
        scope=SoftwareScope.CURRENT_USER,
        architecture=SoftwareArchitecture.X64,
        registry_hive=RegistryHive.CURRENT_USER,
        registry_view=RegistryView.X64,
        registry_key=key,
        uninstall_string=f'"{executable}" --uninstall --quiet',
    )


def build_software_services(
    database_path: Path,
    entries: tuple[RawInstalledSoftwareEntry, ...],
) -> tuple[SoftwareAnalysisServices, AuditRepository, FakeSoftwareInventoryPlatform]:
    platform = FakeSoftwareInventoryPlatform(entries)
    inventory = SoftwareInventoryService(platform)
    resolver = SoftwareTargetResolver(inventory)
    capability = UninstallCapabilityResolver()
    preview = SoftwareUninstallPreviewEngine(
        SoftwareUninstallSafetyPolicy(
            agent_root=Path("D:/Agent"),
            windows_directory=Path("C:/Windows"),
        ),
        capability,
        SoftwareImpactAnalyzer(FakeSystemPlatform()),
        ttl_seconds=300,
    )
    registry = ToolRegistry()
    for tool in (
        SoftwareInventoryTool(inventory),
        SoftwareResolveTool(resolver),
        SoftwareInspectTool(resolver),
        SoftwareUninstallCapabilityTool(resolver, capability),
        SoftwareUninstallPreviewTool(resolver, preview),
    ):
        registry.register(tool)
    guard = SoftwareZeroExecutionGuard()
    guard.validate_registry(registry)
    repository = AuditRepository(database_path)
    repository.initialize()
    service = SoftwareUninstallAnalysisService(
        SoftwareUninstallAnalysisPlanCompiler(),
        registry,
        SoftwareUninstallSafetyValidator(registry, guard),
        guard,
        SoftwareAnalysisConfirmationService(),
        SoftwareUninstallAnalysisAuditLogger(
            repository,
            app_version=__version__,
            git_commit="test",
        ),
    )
    return (
        SoftwareAnalysisServices(
            registry=registry,
            inventory=inventory,
            resolver=resolver,
            service=service,
        ),
        repository,
        platform,
    )
