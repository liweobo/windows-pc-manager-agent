"""Small allowlisted environment for trusted Windows child processes."""

from __future__ import annotations

from collections.abc import Mapping

_SAFE_KEYS = frozenset(
    {
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "WINDIR",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)",
        "TEMP",
        "TMP",
    }
)
_SECRET_FRAGMENTS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "COOKIE",
    "API_KEY",
    "CREDENTIAL",
    "BROKER",
    "RENDEZVOUS",
    "IPC",
)


def sanitized_windows_child_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Copy only non-secret Windows paths; PATH and provider/Broker state are excluded."""
    result: dict[str, str] = {}
    for key, value in environment.items():
        upper = key.upper()
        if upper not in _SAFE_KEYS or any(part in upper for part in _SECRET_FRAGMENTS):
            continue
        if "\x00" in key or "\x00" in value:
            continue
        result[key] = value
    return result
