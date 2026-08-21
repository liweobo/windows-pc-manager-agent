"""Strict parsing and source validation for untrusted Vendor UninstallString data."""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable

from pc_manager_agent.domain.software_uninstall_analysis import (
    RawInstalledSoftwareEntry,
    RegistryHive,
    SoftwareSource,
    canonical_digest,
)
from pc_manager_agent.domain.system_diagnostics import SoftwareScope
from pc_manager_agent.domain.vendor_uninstall import (
    ParsedVendorUninstallMetadata,
    VendorMetadataSourceKind,
    VendorParseConfidence,
)


class VendorMetadataError(ValueError):
    """Raised when raw registry metadata cannot enter the Vendor execution pipeline."""


class VendorUninstallMetadataParser:
    """Parse one exact interactive UninstallString without normalizing its semantics."""

    def __init__(self, argv_parser: Callable[[str], tuple[str, ...]] | None = None) -> None:
        self._argv_parser = argv_parser or windows_command_line_to_argv

    def parse(self, raw: RawInstalledSoftwareEntry) -> ParsedVendorUninstallMetadata:
        """Validate the source and return the exact executable token and argv tokens."""
        if raw.source not in {SoftwareSource.REGISTRY, SoftwareSource.VENDOR}:
            raise VendorMetadataError("Vendor execution requires registry-backed metadata")
        if raw.scope is not SoftwareScope.CURRENT_USER:
            raise VendorMetadataError("Vendor execution supports current-user software only")
        if raw.registry_hive is not RegistryHive.CURRENT_USER:
            raise VendorMetadataError("Vendor execution requires HKEY_CURRENT_USER metadata")
        command_line = raw.uninstall_string
        if command_line is None or not command_line.strip():
            raise VendorMetadataError("Interactive UninstallString is absent")
        if len(command_line) > 32_768 or "\x00" in command_line:
            raise VendorMetadataError("Interactive UninstallString is malformed or oversized")
        if not _quotes_are_structurally_balanced(command_line):
            raise VendorMetadataError("Interactive UninstallString quoting is malformed")
        try:
            argv = self._argv_parser(command_line)
        except (OSError, ValueError) as exc:
            raise VendorMetadataError("Windows command-line parsing failed") from exc
        if not argv or not argv[0].strip():
            raise VendorMetadataError("Interactive UninstallString has no executable token")
        if len(argv) > 33:
            raise VendorMetadataError("Vendor uninstall argument count exceeds the safe limit")
        if any("\x00" in token or len(token) > 32_768 for token in argv):
            raise VendorMetadataError("Vendor uninstall contains an invalid argument token")
        return ParsedVendorUninstallMetadata(
            source_digest=canonical_digest({"interactive_uninstall_string": command_line}),
            executable_token=argv[0].strip(),
            raw_arguments=tuple(argv[1:]),
            source_kind=VendorMetadataSourceKind.INTERACTIVE_UNINSTALL_STRING,
            parse_confidence=VendorParseConfidence.HIGH,
        )


def windows_command_line_to_argv(command_line: str) -> tuple[str, ...]:
    """Apply Windows ``CommandLineToArgvW`` semantics and never invoke a shell."""
    if os.name != "nt":
        raise OSError("Vendor command parsing is available only on Windows")
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    parser = shell32.CommandLineToArgvW
    parser.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parser.restype = ctypes.POINTER(ctypes.c_wchar_p)
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    count = ctypes.c_int(0)
    pointer = parser(command_line, ctypes.byref(count))
    if not pointer:
        raise OSError(ctypes.get_last_error(), "CommandLineToArgvW failed")
    try:
        return tuple(pointer[index] for index in range(count.value))
    finally:
        local_free(pointer)


def _quotes_are_structurally_balanced(command_line: str) -> bool:
    """Reject obvious unmatched quotes while respecting backslash-escaped quotes."""
    inside_quotes = False
    backslashes = 0
    for character in command_line:
        if character == "\\":
            backslashes += 1
            continue
        if character == '"' and backslashes % 2 == 0:
            inside_quotes = not inside_quotes
        backslashes = 0
    return not inside_quotes
