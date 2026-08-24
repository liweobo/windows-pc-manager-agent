"""Conservative Package-to-installed-software identity mapping."""

from __future__ import annotations

from collections.abc import Iterable

from pc_manager_agent.domain.software_uninstall_analysis import (
    NormalizedInstalledSoftware,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareScope
from pc_manager_agent.domain.winget_uninstall import (
    NormalizedWingetPackage,
    WingetMappingConfidence,
    WingetSoftwareMapping,
)


class WingetSoftwareMapper:
    """Require one exact current-user Package ID/version link before execution."""

    def map(
        self,
        package: NormalizedWingetPackage,
        software_entries: Iterable[NormalizedInstalledSoftware],
    ) -> tuple[WingetSoftwareMapping, NormalizedInstalledSoftware | None]:
        """Return HIGH only for one exact structured mapping; visible names never suffice."""
        exact: list[NormalizedInstalledSoftware] = []
        heuristic: list[NormalizedInstalledSoftware] = []
        for software in software_entries:
            identity = software.identity
            if (
                software.scope is SoftwareScope.CURRENT_USER
                and identity.package_id is not None
                and identity.package_id.casefold() == package.package_id.casefold()
                and identity.package_manager_id is not None
                and identity.package_manager_id.casefold() in {"winget", "microsoft.winget"}
                and software.display_version == package.installed_version
            ):
                exact.append(software)
            elif (
                software.scope is SoftwareScope.CURRENT_USER
                and software.display_version == package.installed_version
                and _same(software.display_name, package.package_id)
            ):
                heuristic.append(software)
        if len(exact) == 1:
            selected = exact[0]
            return (
                WingetSoftwareMapping(
                    package_identity_digest=package.identity.canonical_digest(),
                    software_identity_digest=selected.identity.canonical_digest(),
                    confidence=WingetMappingConfidence.HIGH,
                    evidence=(
                        "Package ID, package-manager identifier, installed version, and "
                        "current-user scope matched one software identity.",
                    ),
                ),
                selected,
            )
        if len(exact) > 1:
            return (
                WingetSoftwareMapping(
                    package_identity_digest=package.identity.canonical_digest(),
                    confidence=WingetMappingConfidence.NONE,
                    evidence=("Multiple installed-software identities share the package link.",),
                    warnings=("Ambiguous mappings are never auto-selected.",),
                ),
                None,
            )
        if heuristic:
            return (
                WingetSoftwareMapping(
                    package_identity_digest=package.identity.canonical_digest(),
                    confidence=WingetMappingConfidence.MEDIUM,
                    evidence=("Only visible name/version similarity was observed.",),
                    warnings=("Visible names do not authorize package removal.",),
                ),
                None,
            )
        return (
            WingetSoftwareMapping(
                package_identity_digest=package.identity.canonical_digest(),
                confidence=WingetMappingConfidence.NONE,
                evidence=("No current-user structured Package-to-Software link was found.",),
            ),
            None,
        )


def _same(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()
