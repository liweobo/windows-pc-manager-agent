"""Synthetic Stage 4D2B graph; no real Vendor executable is ever launched."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from tests.fixtures.msi_uninstall import ReadySystemPlatform
from tests.fixtures.software_analysis import FakeSoftwareInventoryPlatform

from pc_manager_agent import __version__
from pc_manager_agent.app.runtime import VendorUninstallServices
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.audit.vendor_uninstall import VendorUninstallAuditLogger
from pc_manager_agent.confirmation.vendor_uninstall import VendorUninstallConfirmationService
from pc_manager_agent.domain.software_uninstall_analysis import (
    RawInstalledSoftwareEntry,
    RegistryHive,
    RegistryView,
    SoftwareSource,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareArchitecture, SoftwareScope
from pc_manager_agent.domain.vendor_uninstall import (
    ValidatedVendorUninstallAction,
    VendorAuthenticodeEvidence,
    VendorAuthenticodeStatus,
    VendorExecutableFileIdentity,
    VendorExecutableObservation,
    VendorInstallLocationRelation,
    VendorProcessExecutionResult,
    VendorProcessResultCategory,
    VendorPublisherMatch,
)
from pc_manager_agent.orchestration.software_capability import UninstallCapabilityResolver
from pc_manager_agent.orchestration.software_inventory import SoftwareInventoryService
from pc_manager_agent.orchestration.software_target_resolver import SoftwareTargetResolver
from pc_manager_agent.orchestration.vendor_execution_preflight import VendorExecutionPreflight
from pc_manager_agent.orchestration.vendor_residual_analyzer import VendorResidualAnalyzer
from pc_manager_agent.orchestration.vendor_uninstall_execution import VendorUninstallService
from pc_manager_agent.orchestration.vendor_uninstall_metadata import VendorUninstallMetadataParser
from pc_manager_agent.orchestration.vendor_uninstall_verifier import VendorUninstallVerifier
from pc_manager_agent.persistence.vendor_uninstall import (
    VendorUninstallExecutionGuard,
    VendorUninstallRepository,
)
from pc_manager_agent.safety.software_uninstall_policy import SoftwareUninstallSafetyPolicy
from pc_manager_agent.safety.vendor_argument_policy import VendorArgumentPolicy
from pc_manager_agent.safety.vendor_executable_trust import VendorExecutableTrustValidator
from pc_manager_agent.safety.vendor_uninstall_policy import VendorUninstallExecutionPolicy
from pc_manager_agent.safety.vendor_uninstall_preview import VendorUninstallPreviewEngine
from pc_manager_agent.safety.vendor_uninstall_validator import VendorUninstallSafetyValidator
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry
from pc_manager_agent.tools.system_tools.vendor_uninstall import VendorUninstallTool


def vendor_entry(
    install_location: Path,
    *,
    name: str = "Example App",
    version: str = "1.0",
    publisher: str = "Example Corporation",
    arguments: str = "/remove",
) -> RawInstalledSoftwareEntry:
    """Build one current-user registry Vendor entry with interactive metadata."""
    executable = install_location / "uninstall.exe"
    return RawInstalledSoftwareEntry(
        raw_source_id=r"HKCU\Software\Example",
        source=SoftwareSource.VENDOR,
        display_name=name,
        display_version=version,
        publisher=publisher,
        install_location=install_location,
        scope=SoftwareScope.CURRENT_USER,
        architecture=SoftwareArchitecture.X64,
        registry_hive=RegistryHive.CURRENT_USER,
        registry_view=RegistryView.X64,
        registry_key=r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Example",
        uninstall_string=f'"{executable}" {arguments}'.strip(),
        quiet_uninstall_string=f'"{executable}" /quiet',
    )


class FakeVendorExecutablePlatform:
    """Return complete synthetic executable trust evidence."""

    def inspect(
        self,
        executable: Path,
        install_location: Path,
        publisher: str,
    ) -> VendorExecutableObservation:
        data = executable.read_bytes()
        metadata = executable.stat()
        return VendorExecutableObservation(
            file_identity=VendorExecutableFileIdentity(
                executable_path=executable,
                volume_serial=1,
                file_id="01",
                size_bytes=len(data),
                created_ns=metadata.st_ctime_ns,
                modified_ns=metadata.st_mtime_ns,
                attributes=0,
                sha256=hashlib.sha256(data).hexdigest(),
            ),
            local_fixed_volume=True,
            reparse_free=True,
            blocked_location=False,
            install_location_relation=VendorInstallLocationRelation.INSIDE_INSTALL_LOCATION,
            authenticode=VendorAuthenticodeEvidence(
                status=VendorAuthenticodeStatus.VALID,
                signer_subject=publisher,
                signer_organization=publisher,
            ),
            publisher_match=VendorPublisherMatch.MATCHED,
        )


class FakeVendorUninstallPlatform:
    """Record a typed action and optionally remove synthetic inventory evidence."""

    def __init__(
        self,
        inventory: FakeSoftwareInventoryPlatform,
        *,
        remove_product: bool = True,
        category: VendorProcessResultCategory = VendorProcessResultCategory.PROCESS_EXITED_ZERO,
    ) -> None:
        self.inventory = inventory
        self.remove_product = remove_product
        self.category = category
        self.calls: list[ValidatedVendorUninstallAction] = []

    def uninstall(
        self,
        action: ValidatedVendorUninstallAction,
        cancellation: CancellationToken,
    ) -> VendorProcessExecutionResult:
        self.calls.append(action)
        if self.remove_product:
            self.inventory.entries = ()
        return VendorProcessExecutionResult(
            category=self.category,
            exit_code=(
                0 if self.category is VendorProcessResultCategory.PROCESS_EXITED_ZERO else 1
            ),
            launched=True,
        )


@dataclass(slots=True)
class SyntheticVendorEnvironment:
    """Resources owned by one full Vendor workflow test."""

    services: VendorUninstallServices
    repository: VendorUninstallRepository
    audit: AuditRepository
    software_inventory: FakeSoftwareInventoryPlatform
    adapter: FakeVendorUninstallPlatform
    raw: RawInstalledSoftwareEntry

    def close(self) -> None:
        """Release both SQLite engines."""
        self.repository.close()
        self.audit.close()


def build_vendor_environment(
    database_path: Path,
    *,
    entry: RawInstalledSoftwareEntry | None = None,
    remove_product: bool = True,
    category: VendorProcessResultCategory = VendorProcessResultCategory.PROCESS_EXITED_ZERO,
) -> SyntheticVendorEnvironment:
    """Compose production orchestration entirely over synthetic local evidence."""
    install_location = database_path.parent / "example-app"
    install_location.mkdir(exist_ok=True)
    executable = install_location / "uninstall.exe"
    executable.write_bytes(b"synthetic signed vendor uninstaller")
    raw = entry or vendor_entry(install_location)
    inventory_platform = FakeSoftwareInventoryPlatform((raw,))
    inventory = SoftwareInventoryService(inventory_platform)
    resolver = SoftwareTargetResolver(inventory)
    repository = VendorUninstallRepository(database_path)
    repository.initialize()
    adapter = FakeVendorUninstallPlatform(
        inventory_platform,
        remove_product=remove_product,
        category=category,
    )
    registry = ToolRegistry(write_guard=VendorUninstallExecutionGuard(repository))
    registry.register(VendorUninstallTool(adapter))
    audit = AuditRepository(database_path)
    audit.initialize()
    service = VendorUninstallService(
        resolver,
        UninstallCapabilityResolver(),
        VendorUninstallMetadataParser(lambda command: _synthetic_argv(command, executable)),
        VendorArgumentPolicy(),
        VendorExecutableTrustValidator(FakeVendorExecutablePlatform()),
        SoftwareUninstallSafetyPolicy(Path("D:/SyntheticAgent"), Path("C:/Windows")),
        VendorUninstallExecutionPolicy(),
        VendorExecutionPreflight(ReadySystemPlatform()),
        VendorUninstallPreviewEngine(),
        VendorUninstallSafetyValidator(registry),
        VendorUninstallConfirmationService(repository),
        repository,
        registry,
        VendorUninstallVerifier(resolver),
        VendorResidualAnalyzer(),
        VendorUninstallAuditLogger(audit, app_version=__version__, git_commit="test"),
        process_is_elevated=lambda: False,
    )
    return SyntheticVendorEnvironment(
        services=VendorUninstallServices(registry=registry, resolver=resolver, service=service),
        repository=repository,
        audit=audit,
        software_inventory=inventory_platform,
        adapter=adapter,
        raw=raw,
    )


def _synthetic_argv(command: str, executable: Path) -> tuple[str, ...]:
    """Parse the controlled fixture tail while production keeps Windows argv semantics."""
    tail = command.rsplit('"', 1)[-1].strip()
    return (str(executable), *tail.split()) if tail else (str(executable),)
