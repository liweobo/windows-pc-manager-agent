"""Conservative normalization over untrusted installed-software source records."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from pc_manager_agent.domain.software_uninstall_analysis import (
    NormalizedInstalledSoftware,
    RawInstalledSoftwareEntry,
    SoftwareIdentity,
    SoftwareInventory,
)
from pc_manager_agent.domain.system_diagnostics import InstalledSoftware
from pc_manager_agent.platform_support.base import CancellationSignal
from pc_manager_agent.platform_support.software_inventory import SoftwareInventoryPlatform

_PRODUCT_CODE = re.compile(
    r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$"
)


@dataclass(frozen=True, slots=True)
class SoftwareInventorySnapshot:
    """Internal pairing of safe inventory and ephemeral raw records by identity."""

    inventory: SoftwareInventory
    raw_by_identity: dict[str, RawInstalledSoftwareEntry]


class SoftwareInventoryService:
    """Collect, normalize, and conservatively deduplicate current software metadata."""

    def __init__(self, platform: SoftwareInventoryPlatform) -> None:
        self._platform = platform

    def collect(
        self,
        max_items: int,
        cancellation: CancellationSignal,
    ) -> SoftwareInventorySnapshot:
        """Build a safe inventory while retaining raw commands only in this ephemeral result."""
        raw_entries, source_warnings, truncated = self._platform.collect_raw(
            max_items, cancellation
        )
        normalized: list[NormalizedInstalledSoftware] = []
        raw_by_identity: dict[str, RawInstalledSoftwareEntry] = {}
        warnings = list(source_warnings)
        source_counts: dict[str, int] = {}
        for raw in raw_entries:
            item = normalize_raw_entry(raw)
            if item is None:
                warnings.append(
                    "One software source entry had no usable display name and was omitted."
                )
                continue
            identity_digest = item.identity.canonical_digest()
            existing_raw = raw_by_identity.get(identity_digest)
            if existing_raw is not None:
                if existing_raw.command_metadata_digest() != raw.command_metadata_digest():
                    warnings.append(
                        "Duplicate software identity had conflicting uninstall metadata and was "
                        "kept "
                        "as the first observed source."
                    )
                continue
            raw_by_identity[identity_digest] = raw
            normalized.append(item)
            source_counts[item.source.value] = source_counts.get(item.source.value, 0) + 1
        normalized.sort(
            key=lambda item: (
                item.display_name.casefold(),
                (item.publisher or "").casefold(),
                item.display_version or "",
                item.scope.value,
                item.architecture.value,
                item.identity.canonical_digest(),
            )
        )
        return SoftwareInventorySnapshot(
            inventory=SoftwareInventory(
                entries=tuple(normalized),
                warnings=tuple(warnings),
                truncated=truncated,
                source_counts=source_counts,
            ),
            raw_by_identity=raw_by_identity,
        )

    def project_legacy(
        self,
        max_items: int,
        cancellation: CancellationSignal,
    ) -> tuple[tuple[InstalledSoftware, ...], tuple[str, ...], bool]:
        """Return the Stage 3 display projection without exposing Stage 4D1 raw metadata."""
        snapshot = self.collect(max_items, cancellation)
        items = tuple(
            InstalledSoftware(
                name=item.display_name,
                version=item.display_version,
                publisher=item.publisher,
                install_date=item.install_date,
                install_location=item.install_location,
                estimated_size_bytes=item.estimated_size_bytes,
                uninstall_entry_present=item.uninstall_metadata_present
                or item.quiet_uninstall_metadata_present,
                scope=item.scope,
                architecture=item.architecture,
                registry_key=item.identity.source_anchor_digest,
            )
            for item in snapshot.inventory.entries
        )
        return items, snapshot.inventory.warnings, snapshot.inventory.truncated


def normalize_raw_entry(raw: RawInstalledSoftwareEntry) -> NormalizedInstalledSoftware | None:
    """Normalize safe fields while retaining each distinct source-qualified identity."""
    display_name = _clean(raw.display_name)
    if display_name is None:
        return None
    version = _clean(raw.display_version)
    publisher = _clean(raw.publisher)
    warnings: list[str] = []
    product_code = _clean(raw.product_code)
    if product_code and not _PRODUCT_CODE.fullmatch(product_code):
        warnings.append("ProductCode metadata is malformed and cannot establish MSI identity.")
        product_code = None
    if raw.windows_installer and product_code is None:
        warnings.append("WindowsInstaller metadata is present without a valid ProductCode.")
    if raw.install_date:
        warnings.append("InstallDate may represent the last installer service or patch date.")
    identity = SoftwareIdentity(
        source=raw.source,
        scope=raw.scope,
        architecture=raw.architecture,
        display_name=display_name,
        display_version=version,
        publisher=publisher,
        source_anchor_digest=raw.source_anchor_digest(),
        product_code=product_code,
        package_manager_id=_clean(raw.package_manager_id),
        package_id=_clean(raw.package_id),
        package_family_name=_clean(raw.package_family_name),
        package_full_name=_clean(raw.package_full_name),
    )
    return NormalizedInstalledSoftware(
        identity=identity,
        display_name=display_name,
        display_version=version,
        publisher=publisher,
        install_location=raw.install_location,
        install_date=_clean(raw.install_date),
        estimated_size_bytes=raw.estimated_size_bytes,
        source=raw.source,
        scope=raw.scope,
        architecture=raw.architecture,
        windows_installer=raw.windows_installer,
        system_component=raw.system_component,
        uninstall_metadata_present=bool(raw.uninstall_string),
        quiet_uninstall_metadata_present=bool(raw.quiet_uninstall_string),
        command_metadata_digest=raw.command_metadata_digest(),
        metadata_warnings=tuple(warnings),
    )


def _clean(value: str | None) -> str | None:
    """Normalize Unicode and whitespace without treating metadata as instructions."""
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value)
    cleaned = " ".join(part for part in normalized.replace("\x00", "").split() if part).strip()
    return cleaned or None
