"""Windows-only non-forcing activity probe for Stage 4E2 candidates."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Any, cast

from pc_manager_agent.platform_support.windows.file_operations import _extended_path

_DELETE = 0x00010000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class WindowsCleanupActivityProbe:
    """Use an ordinary DELETE-access handle; never unlock or terminate anything."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows cleanup activity checks are available only on Windows")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def delete_access_available(self, path: Path) -> bool:
        """Probe DELETE access while respecting existing sharing restrictions."""
        create_file = cast(Any, self._kernel32.CreateFileW)
        create_file.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_void_p,
        ]
        create_file.restype = ctypes.c_void_p
        handle = create_file(
            _extended_path(path),
            _DELETE,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == _INVALID_HANDLE_VALUE:
            return False
        close_handle = cast(Any, self._kernel32.CloseHandle)
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        return bool(close_handle(handle))
