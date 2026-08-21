"""Deterministic identity construction and trust policy for Vendor uninstall executables."""

from __future__ import annotations

import os
import re
from pathlib import Path, PureWindowsPath

from pc_manager_agent.domain.software_uninstall_analysis import (
    NormalizedInstalledSoftware,
    RawInstalledSoftwareEntry,
)
from pc_manager_agent.domain.vendor_uninstall import (
    ParsedVendorUninstallMetadata,
    VendorArgumentAssessment,
    VendorArgumentDecision,
    VendorAuthenticodeStatus,
    VendorExecutableTrustAssessment,
    VendorInstallLocationRelation,
    VendorPublisherMatch,
    VendorTrustDecision,
    VendorUninstallerIdentity,
)
from pc_manager_agent.platform_support.vendor_uninstall import VendorExecutablePlatform

_BLOCKED_EXECUTABLES = frozenset(
    {
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "wscript.exe",
        "cscript.exe",
        "mshta.exe",
        "rundll32.exe",
        "regsvr32.exe",
    }
)
_BLOCKED_SUFFIXES = frozenset({".bat", ".cmd", ".ps1", ".vbs", ".js", ".wsf"})
_PUBLISHER_NOISE = frozenset(
    {"inc", "incorporated", "corp", "corporation", "co", "company", "llc", "ltd", "limited"}
)


class VendorExecutableTrustError(ValueError):
    """Raised when an executable token is structurally outside the supported boundary."""


class VendorExecutableTrustValidator:
    """Resolve one local path, inspect it, and require all conservative trust evidence."""

    def __init__(self, platform: VendorExecutablePlatform) -> None:
        self._platform = platform

    def build_identity(
        self,
        software: NormalizedInstalledSoftware,
        raw: RawInstalledSoftwareEntry,
        parsed: ParsedVendorUninstallMetadata,
        arguments: VendorArgumentAssessment,
    ) -> VendorUninstallerIdentity:
        """Build immutable executable identity from current local evidence."""
        executable = resolve_vendor_executable(parsed.executable_token)
        if executable.name.casefold() in _BLOCKED_EXECUTABLES:
            raise VendorExecutableTrustError("System interpreters and generic loaders are blocked")
        if (
            executable.suffix.casefold() != ".exe"
            or executable.suffix.casefold() in _BLOCKED_SUFFIXES
        ):
            raise VendorExecutableTrustError("Vendor execution accepts only a direct .exe")
        if software.install_location is None or software.publisher is None:
            raise VendorExecutableTrustError("Install location and publisher are required")
        observation = self._platform.inspect(
            executable,
            software.install_location,
            software.publisher,
        )
        reasons: list[str] = []
        evidence: list[str] = []
        if not observation.local_fixed_volume:
            reasons.append("The executable is not on a confirmed local fixed volume.")
        if not observation.reparse_free:
            reasons.append("The executable path contains a reparse point.")
        if observation.blocked_location:
            reasons.append("Temporary, Downloads, and cache locations are blocked.")
        if (
            observation.install_location_relation
            is not VendorInstallLocationRelation.INSIDE_INSTALL_LOCATION
        ):
            reasons.append("The executable is outside the exact known install location.")
        if observation.authenticode.status is not VendorAuthenticodeStatus.VALID:
            reasons.append("A valid offline Authenticode signature was not established.")
        else:
            evidence.append("Offline Authenticode validation succeeded.")
        if observation.publisher_match is not VendorPublisherMatch.MATCHED:
            reasons.append(
                "The signer and installed-software publisher did not conservatively match."
            )
        else:
            evidence.append("Signer organization conservatively matches the installed publisher.")
        if arguments.decision is not VendorArgumentDecision.ALLOW:
            reasons.append("The exact registered arguments are outside the supported policy.")
        decision = (
            VendorTrustDecision.TRUSTED_FOR_EXECUTION
            if not reasons
            else VendorTrustDecision.INSUFFICIENT_EVIDENCE
        )
        return VendorUninstallerIdentity(
            software_identity_hash=software.identity.canonical_digest(),
            registry_source_fingerprint=raw.source_anchor_digest(),
            command_metadata_digest=raw.command_metadata_digest(),
            executable=observation,
            arguments=parsed.raw_arguments,
            argument_assessment=arguments,
            trust=VendorExecutableTrustAssessment(
                decision=decision,
                reasons=tuple(reasons) or ("All mandatory Vendor executable gates passed.",),
                evidence=tuple(evidence),
            ),
        )


def resolve_vendor_executable(token: str) -> Path:
    """Accept only a literal, absolute local Windows path without expansion or PATH search."""
    if not token or "\x00" in token:
        raise VendorExecutableTrustError("Executable token is empty or malformed")
    if "%" in token or token.startswith("~"):
        raise VendorExecutableTrustError("Environment and home expansion are unsupported")
    windows_path = PureWindowsPath(token)
    if not windows_path.is_absolute() or not windows_path.drive:
        raise VendorExecutableTrustError("Vendor executable must be an absolute drive path")
    if token.startswith(("\\\\", "//", "\\?\\UNC\\", "\\.\\")):
        raise VendorExecutableTrustError("UNC and device paths are unsupported")
    if any(part == ".." for part in windows_path.parts):
        raise VendorExecutableTrustError("Parent traversal is unsupported")
    path = Path(os.path.abspath(os.path.normpath(token)))
    if not path.is_absolute():
        raise VendorExecutableTrustError("Executable path normalization was ambiguous")
    return path


def conservative_publisher_match(publisher: str, signer: str | None) -> VendorPublisherMatch:
    """Match normalized significant publisher words without forcing ambiguous equivalence."""
    if not signer:
        return VendorPublisherMatch.UNKNOWN
    left = _publisher_words(publisher)
    right = _publisher_words(signer)
    if not left or not right:
        return VendorPublisherMatch.UNKNOWN
    return VendorPublisherMatch.MATCHED if left == right else VendorPublisherMatch.MISMATCHED


def _publisher_words(value: str) -> tuple[str, ...]:
    """Return deterministic alphanumeric publisher tokens without legal-form noise."""
    words = re.findall(r"[a-z0-9]+", value.casefold())
    return tuple(word for word in words if word not in _PUBLISHER_NOISE)
