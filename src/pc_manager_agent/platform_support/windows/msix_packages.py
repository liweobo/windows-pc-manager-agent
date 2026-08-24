"""Windows WinRT adapter for bounded current-user MSIX operations."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pc_manager_agent.domain.msix_uninstall import (
    MsixDependencyReference,
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
    NormalizedMsixPackage,
    RawMsixPackageRecord,
    ValidatedMsixRemovalAction,
)
from pc_manager_agent.domain.software_uninstall_analysis import (
    RawInstalledSoftwareEntry,
    SoftwareSource,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareArchitecture, SoftwareScope
from pc_manager_agent.platform_support.base import CancellationSignal
from pc_manager_agent.tools.manifest import CancellationToken


class WindowsMsixSoftwarePackageProvider:
    """Project structured current-user MSIX identities into Stage 4D1 software rows."""

    def __init__(self, platform: WindowsMsixPackagePlatform | None = None) -> None:
        self._platform = platform or WindowsMsixPackagePlatform()

    def collect(
        self,
        max_items: int,
        cancellation: CancellationSignal,
    ) -> tuple[tuple[RawInstalledSoftwareEntry, ...], tuple[str, ...], bool]:
        """Return read-only MSIX software rows while preserving exact package anchors."""
        inventory = self._platform.inventory_current_user(max_items, cancellation)
        rows = tuple(
            RawInstalledSoftwareEntry(
                raw_source_id=f"msix|{item.identity.instance.full_name}",
                source=SoftwareSource.MSIX,
                display_name=item.display_name,
                display_version=item.identity.instance.version,
                publisher=item.publisher_display_name,
                install_location=(Path(item.installed_path) if item.installed_path else None),
                scope=SoftwareScope.CURRENT_USER,
                architecture=_software_architecture(item.identity.instance.architecture),
                system_component=(
                    item.identity.package_type
                    in {
                        MsixPackageType.FRAMEWORK,
                        MsixPackageType.RESOURCE,
                        MsixPackageType.SYSTEM,
                    }
                ),
                package_manager_id="msix",
                package_id=item.identity.family.name,
                package_family_name=item.identity.family.family_name,
                package_full_name=item.identity.instance.full_name,
                package_publisher_id=item.identity.family.publisher_id,
            )
            for item in inventory.packages
        )
        return (
            rows,
            inventory.warnings,
            inventory.state is not MsixInventoryState.COMPLETE,
        )


class WindowsMsixPackagePlatform:
    """Use structured WinRT APIs without PowerShell, elevation, or scope expansion."""

    def inventory_current_user(
        self,
        max_items: int,
        cancellation: CancellationSignal,
    ) -> MsixPackageInventory:
        """Enumerate only the current user's registered packages via PackageManager."""
        if max_items <= 0:
            raise ValueError("MSIX inventory limit must be positive")
        if sys.platform != "win32":
            return MsixPackageInventory(
                state=MsixInventoryState.FAILED,
                packages=(),
                warnings=("MSIX inventory is available only on Windows.",),
            )
        try:
            from winrt.windows.management.deployment import PackageManager, PackageTypes

            manager = PackageManager()
            packages: Iterable[Any] = manager.find_packages_by_user_security_id_with_package_types(
                "", PackageTypes.ALL
            )
            normalized: list[NormalizedMsixPackage] = []
            truncated = False
            warnings: list[str] = []
            for index, package in enumerate(packages):
                if cancellation.cancellation_requested():
                    return MsixPackageInventory(
                        state=MsixInventoryState.FAILED,
                        packages=tuple(normalized),
                        warnings=("Current-user MSIX inventory was cancelled.",),
                    )
                if index >= max_items:
                    truncated = True
                    break
                try:
                    raw = _read_package(package)
                    normalized.append(_normalize_package(raw))
                except OSError as exc:
                    truncated = True
                    warnings.append(
                        f"Skipped one package with inaccessible metadata: WinError {exc.winerror}."
                    )
            return MsixPackageInventory(
                state=(MsixInventoryState.TRUNCATED if truncated else MsixInventoryState.COMPLETE),
                packages=tuple(normalized),
                warnings=tuple(warnings)
                + (("Current-user MSIX inventory is incomplete.",) if truncated else ()),
            )
        except Exception as exc:
            return MsixPackageInventory(
                state=MsixInventoryState.FAILED,
                packages=(),
                warnings=(f"Current-user PackageManager inventory failed: {type(exc).__name__}",),
            )

    def remove_current_user(
        self,
        action: ValidatedMsixRemovalAction,
        cancellation: CancellationToken,
    ) -> MsixDeploymentResult:
        """Call only RemovePackageAsync with PreserveRoamableApplicationData."""
        if cancellation.is_cancelled:
            return MsixDeploymentResult(
                category=MsixRemovalResultCategory.CANCELLED_BEFORE_DISPATCH,
                dispatched=False,
            )
        if sys.platform != "win32":
            return MsixDeploymentResult(
                category=MsixRemovalResultCategory.DEPLOYMENT_ERROR,
                error_text="MSIX removal is available only on Windows.",
                dispatched=False,
            )
        try:
            from winrt.windows.management.deployment import PackageManager, RemovalOptions

            manager = PackageManager()
            current: Any = manager.find_package_by_user_security_id_package_full_name(
                "", action.identity.instance.full_name
            )
            if current is None:
                return MsixDeploymentResult(
                    category=MsixRemovalResultCategory.PACKAGE_NOT_FOUND,
                    dispatched=False,
                )
            fresh = _normalize_package(_read_package(current)).identity
            if fresh.canonical_digest() != action.identity.canonical_digest():
                return MsixDeploymentResult(
                    category=MsixRemovalResultCategory.DEPLOYMENT_ERROR,
                    error_text="Package identity changed before dispatch.",
                    dispatched=False,
                )
            operation = manager.remove_package_with_options_async(
                action.identity.instance.full_name,
                RemovalOptions.PRESERVE_ROAMABLE_APPLICATION_DATA,
            )
            result = asyncio.run(_await_deployment(operation, timeout_seconds=3_600.0))
            error_text = str(getattr(result, "error_text", "") or "")[:1_000] or None
            activity = str(getattr(result, "activity_id", "") or "")[:100] or None
            error_code = int(getattr(result, "extended_error_code", 0) or 0)
            return MsixDeploymentResult(
                category=(
                    MsixRemovalResultCategory.REMOVAL_COMPLETED
                    if error_code == 0
                    else _map_hresult_code(error_code)
                ),
                activity_id=activity,
                error_code=error_code or None,
                error_text=error_text,
                dispatched=True,
            )
        except TimeoutError:
            return MsixDeploymentResult(
                category=MsixRemovalResultCategory.INTERRUPTED,
                error_text="Windows deployment monitoring timed out; it will not be retried.",
                dispatched=True,
            )
        except PermissionError as exc:
            return MsixDeploymentResult(
                category=MsixRemovalResultCategory.ACCESS_DENIED,
                error_text=str(exc)[:1_000],
                dispatched=False,
            )
        except OSError as exc:
            return MsixDeploymentResult(
                category=_map_hresult(exc),
                error_code=exc.winerror,
                error_text=str(exc)[:1_000],
                dispatched=True,
            )
        except Exception as exc:
            return MsixDeploymentResult(
                category=MsixRemovalResultCategory.UNKNOWN,
                error_text=f"{type(exc).__name__}: {str(exc)[:900]}",
                dispatched=False,
            )

    def dependency_snapshot(self, identity: MsixPackageIdentity) -> MsixDependencySnapshot:
        """Query documented direct/reverse relations for the exact current-user instance."""
        if sys.platform != "win32":
            return _unavailable_dependencies(identity, "MSIX relationships require Windows.")
        try:
            from winrt.windows.applicationmodel import (
                FindRelatedPackagesOptions,
                PackageRelationship,
            )
            from winrt.windows.management.deployment import PackageManager

            target: Any = PackageManager().find_package_by_user_security_id_package_full_name(
                "", identity.instance.full_name
            )
            if target is None:
                return _unavailable_dependencies(
                    identity, "Target package is no longer registered."
                )
            direct = _related(target, FindRelatedPackagesOptions(PackageRelationship.DEPENDENCIES))
            reverse = _related(target, FindRelatedPackagesOptions(PackageRelationship.DEPENDENTS))
            # Windows may remove dependencies that become unused. V1 blocks whenever a direct
            # dependency exists because it cannot prove Windows will retain that package.
            return MsixDependencySnapshot(
                state=MsixDependencyState.COMPLETE,
                target_identity_digest=identity.canonical_digest(),
                direct_dependencies=direct,
                reverse_dependents=reverse,
                orphan_dependency_risk=bool(direct),
            )
        except Exception as exc:
            return _unavailable_dependencies(
                identity, f"Package relationship query failed: {type(exc).__name__}"
            )


async def _await_deployment(operation: Any, *, timeout_seconds: float) -> Any:
    """Observe one WinRT operation for a fixed bound without adding retry semantics."""
    return await asyncio.wait_for(operation, timeout=timeout_seconds)


def _read_package(package: Any) -> RawMsixPackageRecord:
    """Copy the documented WinRT package properties into an immutable raw record."""
    identity = package.id
    version = identity.version
    status = package.status
    version_text = f"{version.major}.{version.minor}.{version.build}.{version.revision}"
    try:
        has_app_entry = bool(tuple(package.get_app_list_entries()))
    except Exception:
        has_app_entry = False
    return RawMsixPackageRecord(
        family_name=str(identity.family_name),
        full_name=str(identity.full_name),
        name=str(identity.name),
        publisher_id=str(identity.publisher_id),
        publisher_display_name=_optional_text(package.publisher_display_name),
        version=version_text,
        architecture=str(identity.architecture).rsplit(".", 1)[-1].lower(),
        resource_id=str(identity.resource_id or ""),
        display_name=_optional_text(package.display_name),
        description=_optional_text(package.description),
        is_framework=bool(package.is_framework),
        is_resource=bool(package.is_resource_package),
        is_bundle=bool(package.is_bundle),
        is_optional=bool(package.is_optional),
        is_development_mode=bool(package.is_development_mode),
        is_stub=bool(package.is_stub),
        signature_kind=str(getattr(package.signature_kind, "name", package.signature_kind)).lower(),
        status_ok=bool(status.verify_is_ok()),
        has_app_entry=has_app_entry,
        installed_path=_optional_text(package.installed_path),
    )


def _normalize_package(raw: RawMsixPackageRecord) -> NormalizedMsixPackage:
    """Create a typed current-user identity without guessing package safety."""
    package_type = _classify_raw(raw)
    identity = MsixPackageIdentity(
        family=MsixFamilyIdentity(
            family_name=raw.family_name,
            name=raw.name,
            publisher_id=raw.publisher_id,
        ),
        instance=MsixInstanceIdentity(
            full_name=raw.full_name,
            version=raw.version,
            architecture=raw.architecture,
            resource_id=raw.resource_id,
        ),
        scope=MsixScope.CURRENT_USER,
        package_type=package_type,
        current_user_registered=True,
        is_framework=raw.is_framework,
        is_resource=raw.is_resource,
        is_bundle=raw.is_bundle,
        is_optional=raw.is_optional,
        is_development_mode=raw.is_development_mode,
        is_stub=raw.is_stub,
        signature_kind=raw.signature_kind,
        status_ok=raw.status_ok,
    )
    return NormalizedMsixPackage(
        identity=identity,
        display_name=raw.display_name or raw.name,
        publisher_display_name=raw.publisher_display_name,
        installed_path=raw.installed_path,
    )


def _classify_raw(raw: RawMsixPackageRecord) -> MsixPackageType:
    """Classify strong structural signals; protected-name policy runs separately."""
    if raw.is_framework:
        return MsixPackageType.FRAMEWORK
    if raw.is_resource:
        return MsixPackageType.RESOURCE
    if raw.is_bundle:
        return MsixPackageType.BUNDLE
    if raw.is_optional:
        return MsixPackageType.OPTIONAL
    if raw.is_stub or not raw.status_ok or not raw.has_app_entry:
        return MsixPackageType.UNKNOWN
    return MsixPackageType.USER_MSIX_APP


def _related(target: Any, options: Any) -> tuple[MsixDependencyReference, ...]:
    """Return identity-only relationships, including protected package flags."""
    options.include_frameworks = True
    options.include_optionals = True
    options.include_resources = True
    options.include_host_runtimes = True
    values: list[MsixDependencyReference] = []
    for package in target.find_related_packages(options):
        raw = _read_package(package)
        values.append(
            MsixDependencyReference(
                family_name=raw.family_name,
                full_name=raw.full_name,
                package_type=_classify_raw(raw),
            )
        )
    return tuple(sorted(values, key=lambda item: item.full_name.casefold()))


def _unavailable_dependencies(identity: MsixPackageIdentity, reason: str) -> MsixDependencySnapshot:
    """Build a fail-closed relationship result."""
    return MsixDependencySnapshot(
        state=MsixDependencyState.UNAVAILABLE,
        target_identity_digest=identity.canonical_digest(),
        orphan_dependency_risk=True,
        warnings=(reason,),
    )


def _optional_text(value: object) -> str | None:
    """Normalize an optional WinRT string without retaining empty values."""
    text = str(value or "").strip()
    return text or None


def _map_hresult(exc: OSError) -> MsixRemovalResultCategory:
    """Map a small reviewed HRESULT set and leave every other result unknown."""
    return _map_hresult_code(exc.winerror)


def _map_hresult_code(code: int | None) -> MsixRemovalResultCategory:
    """Map a small reviewed HRESULT set and leave every other result as deployment failure."""
    if code in {5, -2147024891}:
        return MsixRemovalResultCategory.ACCESS_DENIED
    if code in {-2147009295, -2147009285}:
        return MsixRemovalResultCategory.PACKAGES_IN_USE
    if code in {-2147009274, -2147009291}:
        return MsixRemovalResultCategory.DEPENDENCY_ERROR
    return MsixRemovalResultCategory.DEPLOYMENT_ERROR


def _software_architecture(value: str) -> SoftwareArchitecture:
    """Map WinRT architecture into the existing conservative software taxonomy."""
    if value == "x86":
        return SoftwareArchitecture.X86
    if value in {"x64", "amd64"}:
        return SoftwareArchitecture.X64
    return SoftwareArchitecture.NATIVE
