"""Parse untrusted Windows uninstall command metadata without executing it."""

from __future__ import annotations

import ctypes
import os
import shlex
from pathlib import Path

from pc_manager_agent.domain.software_uninstall_analysis import (
    ParsedUninstallMetadata,
    canonical_digest,
)

_BLOCKED_WRAPPERS = frozenset(
    {
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "wscript.exe",
        "cscript.exe",
        "rundll32.exe",
        "mshta.exe",
        "regsvr32.exe",
    }
)
_RECOGNIZED_SWITCHES = frozenset(
    {
        "/x",
        "/uninstall",
        "/quiet",
        "/qn",
        "/passive",
        "/norestart",
        "--uninstall",
        "--remove",
        "--silent",
        "--quiet",
    }
)


def parse_windows_uninstall_metadata(command_line: str | None) -> ParsedUninstallMetadata:
    """Return sanitized structural evidence for a command line and never launch it."""
    source_digest = canonical_digest({"command_line": command_line})
    if command_line is None or not command_line.strip():
        return ParsedUninstallMetadata(
            source_digest=source_digest,
            parsed=False,
            warnings=("No uninstall command metadata is present.",),
        )
    if len(command_line) > 32_768:
        return ParsedUninstallMetadata(
            source_digest=source_digest,
            parsed=False,
            warnings=("Uninstall command metadata exceeds the safe parsing limit.",),
        )
    try:
        arguments = _command_line_to_argv(command_line)
    except (OSError, ValueError):
        return ParsedUninstallMetadata(
            source_digest=source_digest,
            parsed=False,
            warnings=("Windows could not parse the uninstall command metadata.",),
        )
    if not arguments:
        return ParsedUninstallMetadata(
            source_digest=source_digest,
            parsed=False,
            warnings=("Uninstall command metadata did not contain an executable.",),
        )
    expanded = os.path.expandvars(arguments[0].strip())
    path = Path(expanded)
    is_unc = expanded.startswith(("\\\\", "//"))
    is_absolute = path.is_absolute()
    wrapper = path.name.casefold() in _BLOCKED_WRAPPERS
    exists = False
    if is_absolute and not is_unc:
        try:
            exists = path.is_file()
        except OSError:
            exists = False
    warnings: list[str] = []
    if is_unc:
        warnings.append("UNC uninstall executables are outside the Stage 4D1 trust boundary.")
    if not is_absolute:
        warnings.append("Relative uninstall executables are unsupported.")
    if wrapper:
        warnings.append("Shell or script-host wrappers are unsupported.")
    if path.suffix.casefold() != ".exe":
        warnings.append("The indicated program is not an executable file.")
    if is_absolute and not is_unc and not exists:
        warnings.append("The indicated uninstall executable does not currently exist.")
    switches = tuple(
        sorted(
            {
                argument.casefold()
                for argument in arguments[1:]
                if argument.casefold() in _RECOGNIZED_SWITCHES
            }
        )
    )
    parsed = bool(
        is_absolute and not is_unc and not wrapper and path.suffix.casefold() == ".exe" and exists
    )
    return ParsedUninstallMetadata(
        source_digest=source_digest,
        parsed=parsed,
        executable_path=path if is_absolute and not is_unc else None,
        executable_exists=exists,
        executable_is_absolute=is_absolute,
        executable_is_unc=is_unc,
        wrapper_detected=wrapper,
        recognized_switches=switches,
        argument_count=max(0, len(arguments) - 1),
        warnings=tuple(warnings),
    )


def _command_line_to_argv(command_line: str) -> tuple[str, ...]:
    """Use CommandLineToArgvW on Windows and a test-only conservative fallback elsewhere."""
    if os.name != "nt":
        return tuple(shlex.split(command_line, posix=False))
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    command_line_to_argv = shell32.CommandLineToArgvW
    command_line_to_argv.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    command_line_to_argv.restype = ctypes.POINTER(ctypes.c_wchar_p)
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    count = ctypes.c_int(0)
    pointer = command_line_to_argv(command_line, ctypes.byref(count))
    if not pointer:
        raise OSError(ctypes.get_last_error(), "CommandLineToArgvW failed")
    try:
        return tuple(pointer[index] for index in range(count.value))
    finally:
        local_free(pointer)
