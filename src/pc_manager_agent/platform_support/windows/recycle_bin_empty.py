"""Exact-volume Windows Recycle Bin inventory and empty adapter."""

from __future__ import annotations

import ctypes
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pythoncom
from win32com.propsys import propsys
from win32com.shell import shell, shellcon

from pc_manager_agent.domain.system_cleanup_execution import RecycleBinInventorySnapshot
from pc_manager_agent.platform_support.windows.recycle_bin import WindowsRecycleBinPlatform

_S_OK = 0
_SHERB_NOCONFIRMATION = 0x00000001
_SHERB_NOPROGRESSUI = 0x00000002
_SHERB_NOSOUND = 0x00000004


class _SHQueryRBInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


class WindowsRecycleBinEmptyPlatform:
    """Use Shell APIs only; never pass a null scope or fall back to file deletion."""

    def __init__(self, capability: WindowsRecycleBinPlatform | None = None) -> None:
        if os.name != "nt":
            raise OSError("Windows Recycle Bin emptying is available only on Windows")
        self._shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        self._capability = capability or WindowsRecycleBinPlatform()

    def inspect(self, volume_root: Path) -> RecycleBinInventorySnapshot:
        """Combine exact-volume aggregate counts with current-user namespace ages."""
        root = self._validate_scope(volume_root)
        capability = self._capability.capability(root)
        if not capability.available or capability.volume_root != root:
            return RecycleBinInventorySnapshot(
                volume_root=root,
                item_count=0,
                observed_size_bytes=0,
                enumeration_complete=False,
                warnings=(capability.reason or "recycle-bin-capability-unavailable",),
            )
        aggregate_count, aggregate_size = self._query(root)
        try:
            count, size, oldest, newest = self._enumerate(root)
        except (OSError, pythoncom.com_error, ValueError, TypeError):
            return RecycleBinInventorySnapshot(
                volume_root=root,
                item_count=aggregate_count,
                observed_size_bytes=aggregate_size,
                enumeration_complete=False,
                warnings=("recycle-bin-shell-enumeration-incomplete",),
            )
        complete = count == aggregate_count and size == aggregate_size
        return RecycleBinInventorySnapshot(
            volume_root=root,
            item_count=aggregate_count,
            observed_size_bytes=aggregate_size,
            oldest_deleted_at=oldest,
            newest_deleted_at=newest,
            enumeration_complete=complete,
            warnings=() if complete else ("recycle-bin-aggregate-mismatch",),
        )

    def empty(self, volume_root: Path) -> int:
        """Call SHEmptyRecycleBinW once for one explicit drive root."""
        root = self._validate_scope(volume_root)
        capability = self._capability.capability(root)
        if not capability.available or capability.volume_root != root:
            raise PermissionError("Exact-volume Recycle Bin capability is unavailable")
        empty = cast(Any, self._shell32.SHEmptyRecycleBinW)
        empty.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
        empty.restype = ctypes.c_long
        return int(
            empty(
                None,
                str(root),
                _SHERB_NOCONFIRMATION | _SHERB_NOPROGRESSUI | _SHERB_NOSOUND,
            )
        )

    def _query(self, root: Path) -> tuple[int, int]:
        info = _SHQueryRBInfo()
        info.cbSize = ctypes.sizeof(info)
        query = cast(Any, self._shell32.SHQueryRecycleBinW)
        query.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(_SHQueryRBInfo)]
        query.restype = ctypes.c_long
        if int(query(str(root), ctypes.byref(info))) != _S_OK:
            raise OSError("SHQueryRecycleBinW failed for the exact volume")
        return max(0, int(info.i64NumItems)), max(0, int(info.i64Size))

    @staticmethod
    def _enumerate(root: Path) -> tuple[int, int, datetime | None, datetime | None]:
        pythoncom.CoInitialize()
        try:
            parent_pidl = shell.SHGetSpecialFolderLocation(0, shellcon.CSIDL_BITBUCKET)
            desktop = cast(Any, shell.SHGetDesktopFolder())
            folder = desktop.BindToObject(parent_pidl, None, shell.IID_IShellFolder)
            enum = folder.EnumObjects(
                0,
                shellcon.SHCONTF_FOLDERS
                | shellcon.SHCONTF_NONFOLDERS
                | shellcon.SHCONTF_INCLUDEHIDDEN,
            )
            date_key = propsys.PSGetPropertyKeyFromName("System.Recycle.DateDeleted")
            size_key = propsys.PSGetPropertyKeyFromName("System.Size")
            source_key = propsys.PSGetPropertyKeyFromName("System.Recycle.DeletedFrom")
            count = total_size = 0
            dates: list[datetime] = []
            while True:
                children = enum.Next(128)
                if not children:
                    break
                for child in children:
                    item = cast(
                        Any,
                        shell.SHCreateItemWithParent(
                            parent_pidl,
                            folder,
                            child,
                            shell.IID_IShellItem2,
                        ),
                    )
                    store = item.GetPropertyStore(0, propsys.IID_IPropertyStore)
                    source = str(store.GetValue(source_key).GetValue())
                    if Path(source).drive.casefold() != root.drive.casefold():
                        continue
                    deleted_at = store.GetValue(date_key).GetValue()
                    if not isinstance(deleted_at, datetime):
                        raise ValueError("Recycle Bin deletion time is unavailable")
                    if deleted_at.tzinfo is None:
                        deleted_at = deleted_at.replace(tzinfo=UTC)
                    dates.append(deleted_at.astimezone(UTC))
                    size_value = store.GetValue(size_key).GetValue()
                    total_size += max(0, int(size_value or 0))
                    count += 1
            return (
                count,
                total_size,
                min(dates, default=None),
                max(dates, default=None),
            )
        finally:
            pythoncom.CoUninitialize()

    @staticmethod
    def _validate_scope(volume_root: Path) -> Path:
        value = Path(os.path.abspath(os.path.normpath(os.fspath(volume_root))))
        system_drive = Path(os.environ.get("SYSTEMROOT", "C:/Windows")).drive
        expected = Path(f"{system_drive}\\")
        if not system_drive or value != expected or not value.is_absolute():
            raise PermissionError("Recycle Bin emptying is limited to the exact system volume")
        return value
