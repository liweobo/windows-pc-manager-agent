"""Strict ProductCode and Windows Installer registration validation."""

from __future__ import annotations

import re
from enum import StrEnum
from uuid import UUID

from pc_manager_agent.domain.software_uninstall_analysis import (
    CapabilitySupport,
    NormalizedInstalledSoftware,
    UninstallCapability,
    UninstallCapabilityType,
    canonical_digest,
)
from pc_manager_agent.domain.software_uninstall_execution import (
    MsiInstallContext,
    ValidatedMsiProduct,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareScope
from pc_manager_agent.platform_support.msi_uninstall import MsiProductInventoryPlatform

_PRODUCT_CODE = re.compile(
    r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$"
)


class MsiProductValidationCode(StrEnum):
    """Fail-closed reasons suitable for UI and audit without command metadata."""

    NOT_MSI_HIGH_CONFIDENCE = "not_msi_high_confidence"
    PRODUCT_CODE_MISSING = "product_code_missing"
    PRODUCT_CODE_INVALID = "product_code_invalid"
    PRODUCT_CODE_CONFLICT = "product_code_conflict"
    PRODUCT_NOT_REGISTERED = "product_not_registered"
    PRODUCT_CONTEXT_AMBIGUOUS = "product_context_ambiguous"
    MACHINE_SCOPE_BLOCKED = "machine_scope_blocked"
    MANAGED_SCOPE_BLOCKED = "managed_scope_blocked"
    SCOPE_MISMATCH = "scope_mismatch"
    PRODUCT_METADATA_MISMATCH = "product_metadata_mismatch"
    PUBLISHER_REQUIRED = "publisher_required"


class MsiProductValidationError(RuntimeError):
    """Raised when local evidence cannot identify one executable MSI product."""

    def __init__(self, code: MsiProductValidationCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_product_code(value: str) -> str:
    """Validate an exact braced MSI GUID and return canonical uppercase form."""
    if not _PRODUCT_CODE.fullmatch(value):
        raise MsiProductValidationError(
            MsiProductValidationCode.PRODUCT_CODE_INVALID,
            "ProductCode is not an exact braced MSI GUID",
        )
    try:
        parsed = UUID(value[1:-1])
    except ValueError as exc:
        raise MsiProductValidationError(
            MsiProductValidationCode.PRODUCT_CODE_INVALID,
            "ProductCode is not a valid GUID",
        ) from exc
    canonical = "{" + str(parsed).upper() + "}"
    if not _PRODUCT_CODE.fullmatch(canonical):
        raise MsiProductValidationError(
            MsiProductValidationCode.PRODUCT_CODE_INVALID,
            "ProductCode canonicalization failed",
        )
    return canonical


class MsiProductValidator:
    """Cross-check Stage 4D1 identity against Windows Installer registration APIs."""

    def __init__(self, platform: MsiProductInventoryPlatform) -> None:
        self._platform = platform

    def validate(
        self,
        software: NormalizedInstalledSoftware,
        capability: UninstallCapability,
    ) -> ValidatedMsiProduct:
        """Return an executable typed product only for one current-user unmanaged MSI."""
        if (
            capability.capability_type is not UninstallCapabilityType.MSI
            or capability.support is not CapabilitySupport.METADATA_SUPPORTED
            or capability.confidence != "high"
        ):
            raise MsiProductValidationError(
                MsiProductValidationCode.NOT_MSI_HIGH_CONFIDENCE,
                "Only high-confidence MSI capability is executable in Stage 4D2A",
            )
        identity_code = software.identity.product_code
        capability_code = capability.product_code
        if identity_code is None or capability_code is None:
            raise MsiProductValidationError(
                MsiProductValidationCode.PRODUCT_CODE_MISSING,
                "MSI ProductCode is missing from local inventory evidence",
            )
        canonical = normalize_product_code(identity_code)
        if normalize_product_code(capability_code) != canonical:
            raise MsiProductValidationError(
                MsiProductValidationCode.PRODUCT_CODE_CONFLICT,
                "Identity and capability ProductCodes differ",
            )
        registrations = tuple(
            item for item in self._platform.registrations(canonical) if item.installed
        )
        if not registrations:
            raise MsiProductValidationError(
                MsiProductValidationCode.PRODUCT_NOT_REGISTERED,
                "Windows Installer does not report this ProductCode as installed",
            )
        if len(registrations) != 1:
            raise MsiProductValidationError(
                MsiProductValidationCode.PRODUCT_CONTEXT_AMBIGUOUS,
                "ProductCode exists in more than one Windows Installer context",
            )
        registration = registrations[0]
        if registration.context is MsiInstallContext.MACHINE:
            raise MsiProductValidationError(
                MsiProductValidationCode.MACHINE_SCOPE_BLOCKED,
                "Machine-wide MSI uninstall requires a future privileged broker",
            )
        if registration.context is MsiInstallContext.USER_MANAGED:
            raise MsiProductValidationError(
                MsiProductValidationCode.MANAGED_SCOPE_BLOCKED,
                "Managed per-user MSI software is outside Stage 4D2A",
            )
        if (
            registration.context is not MsiInstallContext.USER_UNMANAGED
            or software.scope is not SoftwareScope.CURRENT_USER
        ):
            raise MsiProductValidationError(
                MsiProductValidationCode.SCOPE_MISMATCH,
                "Registry scope and Windows Installer context do not agree",
            )
        if not software.publisher or not registration.publisher:
            raise MsiProductValidationError(
                MsiProductValidationCode.PUBLISHER_REQUIRED,
                "An exact publisher is required for executable MSI identity",
            )
        comparisons = (
            (software.display_name, registration.product_name),
            (software.display_version, registration.version),
            (software.publisher, registration.publisher),
        )
        if any(not _same_optional(left, right) for left, right in comparisons):
            raise MsiProductValidationError(
                MsiProductValidationCode.PRODUCT_METADATA_MISMATCH,
                "Windows Installer and uninstall-registry identity metadata differ",
            )
        return ValidatedMsiProduct(
            product_code=canonical,
            product_code_digest=canonical_digest(canonical),
            identity_digest=software.identity.canonical_digest(),
            metadata_digest=software.metadata_digest(),
            capability_digest=capability.canonical_digest(),
            registration_digest=registration.canonical_digest(),
            install_context=registration.context,
            display_name=software.display_name,
            display_version=software.display_version,
            publisher=software.publisher,
            scope=software.scope,
            architecture=software.architecture,
            source_anchor_digest=software.identity.source_anchor_digest,
        )

    def validate_machine(
        self,
        software: NormalizedInstalledSoftware,
        capability: UninstallCapability,
    ) -> ValidatedMsiProduct:
        """Return one exact high-confidence machine MSI for the Stage 4X3 Broker."""
        if (
            capability.capability_type is not UninstallCapabilityType.MSI
            or capability.support is not CapabilitySupport.METADATA_SUPPORTED
            or capability.confidence != "high"
        ):
            raise MsiProductValidationError(
                MsiProductValidationCode.NOT_MSI_HIGH_CONFIDENCE,
                "Only high-confidence MSI capability may enter Stage 4X3",
            )
        identity_code = software.identity.product_code
        capability_code = capability.product_code
        if identity_code is None or capability_code is None:
            raise MsiProductValidationError(
                MsiProductValidationCode.PRODUCT_CODE_MISSING,
                "Machine MSI ProductCode is missing",
            )
        canonical = normalize_product_code(identity_code)
        if normalize_product_code(capability_code) != canonical:
            raise MsiProductValidationError(
                MsiProductValidationCode.PRODUCT_CODE_CONFLICT,
                "Machine MSI identity and capability ProductCodes differ",
            )
        registrations = tuple(
            item for item in self._platform.registrations(canonical) if item.installed
        )
        if len(registrations) != 1:
            raise MsiProductValidationError(
                (
                    MsiProductValidationCode.PRODUCT_NOT_REGISTERED
                    if not registrations
                    else MsiProductValidationCode.PRODUCT_CONTEXT_AMBIGUOUS
                ),
                "Machine ProductCode must have one exact installed registration",
            )
        registration = registrations[0]
        if (
            registration.context is not MsiInstallContext.MACHINE
            or software.scope is not SoftwareScope.LOCAL_MACHINE
        ):
            raise MsiProductValidationError(
                MsiProductValidationCode.SCOPE_MISMATCH,
                "Registry scope and machine Windows Installer context do not agree",
            )
        if not software.publisher or not registration.publisher:
            raise MsiProductValidationError(
                MsiProductValidationCode.PUBLISHER_REQUIRED,
                "An exact publisher is required for machine MSI identity",
            )
        comparisons = (
            (software.display_name, registration.product_name),
            (software.display_version, registration.version),
            (software.publisher, registration.publisher),
        )
        if any(not _same_optional(left, right) for left, right in comparisons):
            raise MsiProductValidationError(
                MsiProductValidationCode.PRODUCT_METADATA_MISMATCH,
                "Machine MSI registration and software identity metadata differ",
            )
        return ValidatedMsiProduct(
            product_code=canonical,
            product_code_digest=canonical_digest(canonical),
            identity_digest=software.identity.canonical_digest(),
            metadata_digest=software.metadata_digest(),
            capability_digest=capability.canonical_digest(),
            registration_digest=registration.canonical_digest(),
            install_context=registration.context,
            display_name=software.display_name,
            display_version=software.display_version,
            publisher=software.publisher,
            scope=software.scope,
            architecture=software.architecture,
            source_anchor_digest=software.identity.source_anchor_digest,
        )


def _same_optional(left: str | None, right: str | None) -> bool:
    """Compare identity text without inventing missing values."""
    if left is None or right is None:
        return left is None and right is None
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()
