"""Default-deny argument policy for interactive Vendor uninstallers."""

from __future__ import annotations

import re
from pathlib import PureWindowsPath

from pc_manager_agent.domain.software_uninstall_analysis import canonical_digest
from pc_manager_agent.domain.vendor_uninstall import (
    VendorArgumentAssessment,
    VendorArgumentDecision,
)

_ALLOWED_EXACT = frozenset(
    {
        "/uninstall",
        "--uninstall",
        "-uninstall",
        "/remove",
        "--remove",
        "-remove",
    }
)
_QUIET_OR_RESTART = (
    "quiet",
    "silent",
    "passive",
    "norestart",
    "restart",
    "reboot",
    "qn",
)
_DATA_OR_PATH = (
    "data",
    "settings",
    "profile",
    "cache",
    "log",
    "dir",
    "path",
    "target",
    "file",
)
_SCRIPT_SUFFIXES = frozenset({".bat", ".cmd", ".ps1", ".vbs", ".js", ".wsf"})
_SHELL_META = re.compile(r"[|&<>`\r\n]")


class VendorArgumentPolicy:
    """Allow no arguments or one finite interactive removal verb unchanged."""

    def assess(self, arguments: tuple[str, ...]) -> VendorArgumentAssessment:
        """Classify the exact argv vector without adding, removing, or rewriting tokens."""
        fingerprint = canonical_digest(arguments)
        if not arguments:
            return VendorArgumentAssessment(
                decision=VendorArgumentDecision.ALLOW,
                argument_fingerprint=fingerprint,
                argument_count=0,
                reasons=("The registered interactive uninstaller requires no arguments.",),
            )
        reasons: list[str] = []
        if len(arguments) != 1:
            reasons.append(
                "Only zero arguments or one known interactive removal verb is supported."
            )
        for argument in arguments:
            folded = argument.strip().casefold()
            if _SHELL_META.search(argument) or "$(" in argument:
                reasons.append("Shell-like metacharacters are not accepted.")
            if argument.startswith("@"):
                reasons.append("Response-file arguments are unsupported.")
            if PureWindowsPath(argument).suffix.casefold() in _SCRIPT_SUFFIXES:
                reasons.append("Script arguments are unsupported.")
            if any(term in folded for term in _QUIET_OR_RESTART):
                reasons.append("Quiet, restart, and reboot arguments are unsupported.")
            if any(term in folded for term in _DATA_OR_PATH):
                reasons.append("Data-deletion and path-bearing arguments are unsupported.")
            if folded not in _ALLOWED_EXACT:
                reasons.append("An argument is outside the finite interactive allowlist.")
        if reasons:
            return VendorArgumentAssessment(
                decision=VendorArgumentDecision.BLOCK,
                argument_fingerprint=fingerprint,
                argument_count=len(arguments),
                reasons=tuple(dict.fromkeys(reasons)),
            )
        return VendorArgumentAssessment(
            decision=VendorArgumentDecision.ALLOW,
            argument_fingerprint=fingerprint,
            argument_count=len(arguments),
            reasons=("The exact registered argument is a known interactive removal verb.",),
        )
