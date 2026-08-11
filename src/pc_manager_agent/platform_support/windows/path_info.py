"""Read-only Windows volume and last-access-time capability probes."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Any, cast


def is_network_path(path: Path) -> bool:
    """Return whether a root is UNC/device syntax or a mapped remote drive."""
    raw = str(path)
    if raw.startswith(("\\\\", "//")):
        return True
    if os.name != "nt":
        return False
    anchor = path.anchor
    if not anchor:
        return False
    if not anchor.endswith(("\\", "/")):
        anchor += "\\"
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_drive_type = cast(Any, kernel32.GetDriveTypeW)
    return int(get_drive_type(anchor)) == 4


def last_access_time_reliable(root: Path) -> bool | None:
    """Conservatively report whether NTFS last-access updates are enabled."""
    if os.name != "nt" or _filesystem_name(root).casefold() != "ntfs":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\FileSystem",
        ) as key:
            value, _value_type = winreg.QueryValueEx(key, "NtfsDisableLastAccessUpdate")
    except OSError:
        return None
    if value == 0:
        return True
    if value == 1:
        return False
    return None


def _filesystem_name(root: Path) -> str:
    """Return the Windows filesystem name or an empty string on uncertainty."""
    if os.name != "nt" or not root.anchor:
        return ""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_volume_information = cast(Any, kernel32.GetVolumeInformationW)
    filesystem = ctypes.create_unicode_buffer(32)
    success = get_volume_information(
        root.anchor,
        None,
        0,
        None,
        None,
        None,
        filesystem,
        len(filesystem),
    )
    return filesystem.value if success else ""
