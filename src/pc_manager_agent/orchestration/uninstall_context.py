"""Capture reliable pre-uninstall evidence for later Stage 4D3 analysis."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pc_manager_agent.domain.msix_uninstall import MsixUninstallPreview
from pc_manager_agent.domain.software_residuals import (
    ContextPathEvidence,
    ResidualClassification,
    ResidualSource,
    UninstallContext,
    UninstallMechanism,
)
from pc_manager_agent.domain.software_uninstall_execution import MsiUninstallPreview
from pc_manager_agent.domain.vendor_uninstall import VendorUninstallPreview
from pc_manager_agent.domain.winget_uninstall import WingetUninstallPreview
from pc_manager_agent.persistence.software_residuals import (
    SoftwareResidualRepository,
    SoftwareResidualStoreError,
)

_LOG = logging.getLogger(__name__)


class UninstallContextRecorder:
    """Store best-effort Stage 4D3 evidence without granting uninstall authority."""

    def __init__(self, repository: SoftwareResidualRepository) -> None:
        self._repository = repository

    def capture_msi(self, preview: MsiUninstallPreview) -> UUID | None:
        """Capture exact MSI identity and install-location evidence before dispatch."""
        known = _install_location_evidence(
            preview.target.install_location,
            preview.target.publisher,
            preview.target.display_name,
        )
        context = UninstallContext(
            transaction_id=preview.transaction_id,
            mechanism=UninstallMechanism.MSI,
            software_identity_digest=preview.identity_digest,
            display_name=preview.target.display_name,
            display_version=preview.target.display_version,
            publisher=preview.target.publisher,
            scope=preview.target.scope.value,
            architecture=preview.target.architecture.value,
            original_install_location=preview.target.install_location,
            msi_product_code=preview.validated_product.product_code,
            known_paths=known,
            uninstall_started_at=datetime.now(UTC),
            warnings=_missing_path_warning(known),
        )
        return self._store_draft(context)

    def capture_vendor(self, preview: VendorUninstallPreview) -> UUID | None:
        """Capture exact Vendor software metadata without persisting its raw command."""
        known = _install_location_evidence(
            preview.target.install_location,
            preview.target.publisher,
            preview.target.display_name,
        )
        context = UninstallContext(
            transaction_id=preview.transaction_id,
            mechanism=UninstallMechanism.VENDOR,
            software_identity_digest=preview.identity_digest,
            display_name=preview.target.display_name,
            display_version=preview.target.display_version,
            publisher=preview.target.publisher,
            scope=preview.target.scope.value,
            architecture=preview.target.architecture.value,
            original_install_location=preview.target.install_location,
            known_paths=known,
            uninstall_started_at=datetime.now(UTC),
            warnings=_missing_path_warning(known),
        )
        return self._store_draft(context)

    def capture_winget(self, preview: WingetUninstallPreview) -> UUID | None:
        """Capture exact official-source package and mapped software evidence."""
        known = _install_location_evidence(
            preview.software.install_location,
            preview.software.publisher,
            preview.software.display_name,
        )
        identity = preview.package.identity
        context = UninstallContext(
            transaction_id=preview.transaction_id,
            mechanism=UninstallMechanism.WINGET,
            software_identity_digest=preview.software.identity.canonical_digest(),
            display_name=preview.software.display_name,
            display_version=preview.software.display_version,
            publisher=preview.software.publisher,
            scope=preview.software.scope.value,
            architecture=preview.software.architecture.value,
            original_install_location=preview.software.install_location,
            package_id=identity.package_id,
            package_source=identity.source_name,
            known_paths=known,
            uninstall_started_at=datetime.now(UTC),
            warnings=_missing_path_warning(known),
        )
        return self._store_draft(context)

    def capture_msix(self, preview: MsixUninstallPreview) -> UUID | None:
        """Capture exact Package identity and strongly protected package-data path."""
        package = preview.package
        identity = package.identity
        install_location = Path(package.installed_path) if package.installed_path else None
        known: list[ContextPathEvidence] = []
        if install_location is not None:
            known.append(
                ContextPathEvidence(
                    path=install_location,
                    source=ResidualSource.MSIX_INSTALL_LOCATION,
                    evidence_code="msix-installed-path-exact",
                    expected_classification=ResidualClassification.PROGRAM_RESIDUAL,
                    max_depth=4,
                )
            )
        package_data = _msix_package_data_path(identity.family.family_name)
        if package_data is not None:
            known.append(
                ContextPathEvidence(
                    path=package_data,
                    source=ResidualSource.MSIX_PACKAGE_DATA,
                    evidence_code="msix-family-path-exact",
                    expected_classification=ResidualClassification.PACKAGE_USER_DATA,
                    max_depth=2,
                )
            )
        warnings: tuple[str, ...] = ()
        if not known:
            warnings = ("No exact filesystem path was available before MSIX removal.",)
        context = UninstallContext(
            transaction_id=preview.transaction_id,
            mechanism=UninstallMechanism.MSIX,
            software_identity_digest=identity.canonical_digest(),
            display_name=package.display_name,
            display_version=identity.instance.version,
            publisher=package.publisher_display_name,
            scope=identity.scope.value,
            architecture=identity.instance.architecture,
            original_install_location=install_location,
            msix_family_name=identity.family.family_name,
            msix_full_name=identity.instance.full_name,
            known_paths=tuple(known),
            uninstall_started_at=datetime.now(UTC),
            warnings=warnings,
        )
        return self._store_draft(context)

    def finalize(
        self,
        context_id: UUID | None,
        *,
        verification_state: str,
        verified_removed: bool,
        completed_unverified: bool = False,
        completed_at: datetime | None = None,
    ) -> bool:
        """Finalize one captured context; unsupported outcomes remain ineligible."""
        if context_id is None:
            return False
        try:
            context = self._repository.get_context(context_id)
            normalized_state = (
                "completed_unverified" if completed_unverified else verification_state
            )
            completed = context.model_copy(
                update={
                    "uninstall_completed_at": completed_at or datetime.now(UTC),
                    "verification_state": normalized_state,
                    "verified_removed": verified_removed,
                    "context_complete": True,
                }
            )
            self._repository.upsert_context(completed)
        except SoftwareResidualStoreError as exc:
            _LOG.warning(
                "Stage 4D3 uninstall context finalization failed",
                extra={"context_id": str(context_id), "error_code": type(exc).__name__},
            )
            return False
        return True

    def _store_draft(self, context: UninstallContext) -> UUID | None:
        try:
            self._repository.upsert_context(context)
        except SoftwareResidualStoreError as exc:
            _LOG.warning(
                "Stage 4D3 uninstall context capture failed",
                extra={
                    "transaction_id": str(context.transaction_id),
                    "error_code": type(exc).__name__,
                },
            )
            return None
        return context.context_id


def _install_location_evidence(
    path: Path | None,
    publisher: str | None,
    display_name: str,
) -> tuple[ContextPathEvidence, ...]:
    if path is None:
        return ()
    return (
        ContextPathEvidence(
            path=path,
            source=ResidualSource.INSTALL_LOCATION,
            evidence_code="install-location-exact",
            expected_classification=ResidualClassification.PROGRAM_RESIDUAL,
            max_depth=6,
            shared_location=_looks_like_shared_publisher_root(path, publisher, display_name),
        ),
    )


def _looks_like_shared_publisher_root(
    path: Path,
    publisher: str | None,
    display_name: str,
) -> bool:
    """Flag an exact publisher root without claiming the whole location for one product."""
    leaf = "".join(character for character in path.name.casefold() if character.isalnum())
    publisher_key = "".join(
        character for character in (publisher or "").casefold() if character.isalnum()
    )
    product_key = "".join(character for character in display_name.casefold() if character.isalnum())
    return bool(leaf and publisher_key and leaf == publisher_key and leaf != product_key)


def _missing_path_warning(known: tuple[ContextPathEvidence, ...]) -> tuple[str, ...]:
    if known:
        return ()
    return ("No exact install location was available; Stage 4D3 will not search by name.",)


def _msix_package_data_path(family_name: str) -> Path | None:
    """Build one exact current-user package path without accepting path separators."""
    if (
        not family_name
        or family_name in {".", ".."}
        or Path(family_name).name != family_name
        or any(separator in family_name for separator in ("/", "\\"))
    ):
        return None
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    return Path(local_app_data) / "Packages" / family_name
