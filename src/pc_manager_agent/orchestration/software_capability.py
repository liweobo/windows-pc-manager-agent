"""Resolve uninstall capability metadata without creating an execution path."""

from __future__ import annotations

import re

from pc_manager_agent.domain.software_uninstall_analysis import (
    CapabilitySupport,
    NormalizedInstalledSoftware,
    RawInstalledSoftwareEntry,
    SoftwareSource,
    UninstallCapability,
    UninstallCapabilityType,
)
from pc_manager_agent.platform_support.windows.uninstall_metadata import (
    parse_windows_uninstall_metadata,
)

_PRODUCT_CODE = re.compile(
    r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$"
)


class UninstallCapabilityResolver:
    """Classify one source record's metadata; capability never implies permission."""

    def resolve(
        self,
        software: NormalizedInstalledSoftware,
        raw: RawInstalledSoftwareEntry,
    ) -> UninstallCapability:
        """Return one mechanism, ambiguity, or unsupported result with sanitized evidence."""
        if software.identity.canonical_digest() != _identity_digest_for(raw, software):
            return _unsupported("Raw source no longer matches the normalized software identity.")
        if software.source is SoftwareSource.DRIVER_PACKAGE:
            return UninstallCapability(
                capability_type=UninstallCapabilityType.DRIVER_PACKAGE,
                support=CapabilitySupport.UNSUPPORTED,
                confidence="high",
                evidence=("The structured source identifies a driver package.",),
                warnings=("Driver removal is outside this workflow.",),
            )
        if software.source is SoftwareSource.WINDOWS_FEATURE:
            return UninstallCapability(
                capability_type=UninstallCapabilityType.WINDOWS_COMPONENT,
                support=CapabilitySupport.UNSUPPORTED,
                confidence="high",
                evidence=("The structured source identifies a Windows feature.",),
                warnings=("Windows feature changes require a separate system operation.",),
            )
        if software.source is SoftwareSource.MSIX:
            family = software.identity.package_family_name
            if family and software.identity.package_full_name:
                return UninstallCapability(
                    capability_type=UninstallCapabilityType.MSIX,
                    support=CapabilitySupport.METADATA_SUPPORTED,
                    confidence="high",
                    evidence=("Exact current-user package family and full names are present.",),
                    package_family_name=family,
                )
            return _unsupported("MSIX metadata lacks an exact package family or full name.")
        if software.source is SoftwareSource.PACKAGE_MANAGER:
            provider = software.identity.package_manager_id
            package_id = software.identity.package_id
            if provider and package_id:
                return UninstallCapability(
                    capability_type=UninstallCapabilityType.PACKAGE_MANAGER,
                    support=CapabilitySupport.METADATA_SUPPORTED,
                    confidence="high",
                    evidence=("A structured package provider and exact package ID are present.",),
                    package_manager_id=provider,
                    package_id=package_id,
                )
            return _unsupported("Package-manager source lacks explicit provider provenance.")
        if software.source is SoftwareSource.PORTABLE:
            return UninstallCapability(
                capability_type=UninstallCapabilityType.PORTABLE,
                support=CapabilitySupport.UNSUPPORTED,
                confidence="medium",
                evidence=("The structured source explicitly identifies portable software.",),
                warnings=("Stage 4D1 does not infer or delete portable application folders.",),
            )
        product_code = software.identity.product_code
        if software.windows_installer is True:
            if product_code and _PRODUCT_CODE.fullmatch(product_code):
                return UninstallCapability(
                    capability_type=UninstallCapabilityType.MSI,
                    support=CapabilitySupport.METADATA_SUPPORTED,
                    confidence="high",
                    evidence=("WindowsInstaller=1 and a valid ProductCode agree.",),
                    warnings=("The ProductCode is metadata only; MSI will not be invoked.",),
                    product_code=product_code.upper(),
                )
            return _unsupported("WindowsInstaller metadata conflicts with a missing ProductCode.")
        command = raw.quiet_uninstall_string or raw.uninstall_string
        parsed = parse_windows_uninstall_metadata(command)
        if command is not None:
            return UninstallCapability(
                capability_type=UninstallCapabilityType.VENDOR_UNINSTALLER,
                support=(
                    CapabilitySupport.METADATA_SUPPORTED
                    if parsed.parsed
                    else CapabilitySupport.UNSUPPORTED
                ),
                confidence="medium" if parsed.parsed else "low",
                evidence=("A vendor uninstall command was present and parsed as untrusted data.",),
                warnings=parsed.warnings,
                parsed_metadata=parsed,
            )
        return UninstallCapability(
            capability_type=UninstallCapabilityType.UNKNOWN,
            support=CapabilitySupport.UNSUPPORTED,
            confidence="low",
            evidence=("No supported uninstall mechanism metadata was found.",),
            warnings=("No future execution method can be derived safely.",),
        )


def _identity_digest_for(
    raw: RawInstalledSoftwareEntry,
    software: NormalizedInstalledSoftware,
) -> str:
    """Rebuild the expected anchor check without retaining the raw entry."""
    if raw.source_anchor_digest() != software.identity.source_anchor_digest:
        return ""
    return software.identity.canonical_digest()


def _unsupported(reason: str) -> UninstallCapability:
    return UninstallCapability(
        capability_type=UninstallCapabilityType.UNSUPPORTED,
        support=CapabilitySupport.UNSUPPORTED,
        confidence="low",
        evidence=(reason,),
        warnings=("Stage 4D1 stopped capability analysis safely.",),
    )
