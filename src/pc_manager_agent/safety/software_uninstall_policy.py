"""Fail-closed deterministic policy for Stage 4D1 software analysis."""

from __future__ import annotations

import os
from pathlib import Path

from pc_manager_agent.domain.software_uninstall_analysis import (
    NormalizedInstalledSoftware,
    SoftwareSafetyAssessment,
    SoftwareSafetyClass,
    SoftwareSafetyDecision,
    SoftwareSource,
)

_DEVELOPER_RUNTIME = (
    "runtime",
    "redistributable",
    "visual c++",
    ".net",
    "jdk",
    "jre",
    "python",
    "node.js",
)
_DEVELOPER_TOOL = ("visual studio", "jetbrains", "git", "sdk", "compiler", "ide")
_DATABASE = ("sql server", "postgres", "mysql", "mariadb", "mongodb", "oracle database")
_BACKGROUND = ("docker", "container", "virtualbox", "vmware", "hyper-v", "server")
_SECURITY = (
    "antivirus",
    "anti-virus",
    "defender",
    "endpoint protection",
    "firewall",
    "security agent",
    "edr",
)
_NETWORK = ("vpn", "network adapter", "packet filter", "winsock", "proxy client")
_DRIVER = ("driver", "驱动", "chipset", "firmware")
_HARDWARE = ("nvidia", "amd software", "intel graphics", "realtek", "synaptics")
_ENTERPRISE = ("corporate", "enterprise management", "mdm", "company portal")
_PACKAGE_MANAGER = ("winget", "chocolatey", "scoop", "package manager")


class SoftwareUninstallSafetyPolicy:
    """Classify software conservatively; the decision can only allow a read-only Preview."""

    def __init__(self, agent_root: Path, windows_directory: Path | None = None) -> None:
        self._agent_root = _canonical(agent_root)
        default_windows = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        self._windows_directory = _canonical(windows_directory or default_windows)

    def assess(self, software: NormalizedInstalledSoftware) -> SoftwareSafetyAssessment:
        """Return a deterministic classification with explicit evidence and reasons."""
        combined = " ".join(
            value.casefold() for value in (software.display_name, software.publisher or "") if value
        )
        location = _canonical(software.install_location) if software.install_location else None
        if location is not None and _is_within(location, self._agent_root):
            return _assessment(
                SoftwareSafetyClass.AGENT_COMPONENT,
                SoftwareSafetyDecision.BLOCKED,
                ("Install location is within the running Agent code root.",),
                ("The Agent must never prepare removal of itself or its components.",),
            )
        if "windows pc manager agent" in combined or "pc_manager_agent" in combined:
            return _assessment(
                SoftwareSafetyClass.AGENT_COMPONENT,
                SoftwareSafetyDecision.BLOCKED,
                ("Product identity matches this Agent.",),
                ("Agent components are protected.",),
            )
        if software.source is SoftwareSource.DRIVER_PACKAGE or _contains(combined, _DRIVER):
            return _assessment(
                SoftwareSafetyClass.DEVICE_DRIVER,
                SoftwareSafetyDecision.BLOCKED,
                ("Driver or firmware metadata was detected.",),
                ("Driver packages are outside Stage 4D1 and future generic uninstall execution.",),
            )
        if software.source is SoftwareSource.WINDOWS_FEATURE:
            return _assessment(
                SoftwareSafetyClass.WINDOWS_FEATURE,
                SoftwareSafetyDecision.BLOCKED,
                ("Source identifies a Windows optional feature.",),
                ("Windows feature changes require a separate R3 design.",),
            )
        microsoft = "microsoft" in (software.publisher or "").casefold()
        if software.system_component is True or (
            location is not None and _is_within(location, self._windows_directory)
        ):
            return _assessment(
                SoftwareSafetyClass.WINDOWS_COMPONENT,
                SoftwareSafetyDecision.BLOCKED,
                ("SystemComponent metadata or a Windows-directory location was detected.",),
                ("Windows components are protected.",),
            )
        if microsoft and (
            "windows" in combined
            or software.source in {SoftwareSource.MSIX, SoftwareSource.REGISTRY}
        ):
            return _assessment(
                SoftwareSafetyClass.WINDOWS_COMPONENT,
                SoftwareSafetyDecision.BLOCKED,
                ("Microsoft publisher and protected package/registry source require caution.",),
                ("Ambiguous Microsoft platform components are blocked by default.",),
            )
        if _contains(combined, _SECURITY):
            return _assessment(
                SoftwareSafetyClass.SECURITY_SOFTWARE,
                SoftwareSafetyDecision.BLOCKED,
                ("Security-product terminology was detected in local metadata.",),
                ("Security software is protected from generic removal workflows.",),
            )
        if _contains(combined, _ENTERPRISE):
            return _assessment(
                SoftwareSafetyClass.ENTERPRISE_MANAGED,
                SoftwareSafetyDecision.BLOCKED,
                ("Enterprise-management terminology was detected.",),
                ("Managed software may be controlled by organizational policy.",),
            )
        if _contains(combined, _PACKAGE_MANAGER):
            return _assessment(
                SoftwareSafetyClass.PACKAGE_MANAGER,
                SoftwareSafetyDecision.PREVIEW_HIGH_IMPACT,
                ("Package-manager identity was detected.",),
                ("Removing a package manager can affect management of other software.",),
            )
        if _contains(combined, _NETWORK):
            return _assessment(
                SoftwareSafetyClass.VPN_OR_NETWORK_COMPONENT,
                SoftwareSafetyDecision.PREVIEW_HIGH_IMPACT,
                ("VPN or network-component terminology was detected.",),
                ("Network connectivity may be affected by a future removal.",),
            )
        if _contains(combined, _DATABASE):
            return _assessment(
                SoftwareSafetyClass.DATABASE_SERVER,
                SoftwareSafetyDecision.PREVIEW_HIGH_IMPACT,
                ("Database-server identity was detected.",),
                ("A future removal may affect services and user databases.",),
            )
        if _contains(combined, _BACKGROUND):
            return _assessment(
                SoftwareSafetyClass.BACKGROUND_PLATFORM,
                SoftwareSafetyDecision.PREVIEW_HIGH_IMPACT,
                ("Background platform or virtualization identity was detected.",),
                ("Other applications may rely on this platform.",),
            )
        if _contains(combined, _DEVELOPER_RUNTIME):
            return _assessment(
                SoftwareSafetyClass.DEVELOPER_RUNTIME,
                SoftwareSafetyDecision.PREVIEW_HIGH_IMPACT,
                ("Developer runtime or redistributable identity was detected.",),
                ("Multiple tools may share this runtime.",),
            )
        if _contains(combined, _HARDWARE):
            return _assessment(
                SoftwareSafetyClass.HARDWARE_UTILITY,
                SoftwareSafetyDecision.PREVIEW_HIGH_IMPACT,
                ("Hardware-vendor utility identity was detected.",),
                ("Device features may depend on this utility.",),
            )
        if _contains(combined, _DEVELOPER_TOOL):
            return _assessment(
                SoftwareSafetyClass.DEVELOPER_TOOL,
                SoftwareSafetyDecision.PREVIEW_ALLOWED,
                ("Developer-tool identity was detected.",),
                ("A future removal may affect local development workflows.",),
            )
        if software.publisher and software.source in {
            SoftwareSource.MSI,
            SoftwareSource.VENDOR,
            SoftwareSource.PACKAGE_MANAGER,
            SoftwareSource.MSIX,
        }:
            return _assessment(
                SoftwareSafetyClass.USER_APPLICATION,
                SoftwareSafetyDecision.PREVIEW_ALLOWED,
                ("A named publisher and supported application metadata source are present.",),
                ("Stage 4D1 may display analysis but still cannot uninstall it.",),
            )
        return _assessment(
            SoftwareSafetyClass.UNKNOWN,
            SoftwareSafetyDecision.BLOCKED,
            ("Metadata is insufficient for a safe application classification.",),
            ("Unknown software is blocked by default.",),
        )


def _assessment(
    safety_class: SoftwareSafetyClass,
    decision: SoftwareSafetyDecision,
    evidence: tuple[str, ...],
    reasons: tuple[str, ...],
) -> SoftwareSafetyAssessment:
    return SoftwareSafetyAssessment(
        safety_class=safety_class,
        decision=decision,
        evidence=evidence,
        reasons=reasons,
    )


def _contains(value: str, terms: tuple[str, ...]) -> bool:
    return any(term in value for term in terms)


def _canonical(path: Path) -> Path:
    return Path(os.path.normcase(str(path.resolve(strict=False))))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
