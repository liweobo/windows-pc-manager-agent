"""Windows Shell Recycle Bin adapter with no legacy or permanent-delete fallback."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Any, ClassVar, cast

import pythoncom
import pywintypes
from win32com.server.policy import DesignatedWrapPolicy
from win32com.shell import shell, shellcon

from pc_manager_agent.domain.trash import (
    RecycleBinCapability,
    RecycleBinResult,
    RecycleVerificationStatus,
)

_DRIVE_FIXED = 3
_FILE_READ_ONLY_VOLUME = 0x00080000
_FOFX_RECYCLEONDELETE = 0x00080000
_FOFX_ADDUNDORECORD = 0x20000000
_S_OK = 0
_E_FAIL = -2147467259


def _hresult_succeeded(value: int) -> bool:
    """Apply COM SUCCEEDED semantics, including informative success status codes."""
    return value & 0x80000000 == 0


class RecycleBinPlatformError(OSError):
    """Raised when the Shell cannot prove an object entered the Recycle Bin."""


class _SHQueryRBInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


class _RecycleProgressSink(DesignatedWrapPolicy):
    """Capture the actual delete HRESULT and newly created Recycle Bin Shell item."""

    _com_interfaces_: ClassVar[list[object]] = [shell.IID_IFileOperationProgressSink]
    _public_methods_: ClassVar[list[str]] = [
        "StartOperations",
        "FinishOperations",
        "PreRenameItem",
        "PostRenameItem",
        "PreMoveItem",
        "PostMoveItem",
        "PreCopyItem",
        "PostCopyItem",
        "PreDeleteItem",
        "PostDeleteItem",
        "PreNewItem",
        "PostNewItem",
        "UpdateProgress",
        "ResetTimer",
        "PauseTimer",
        "ResumeTimer",
    ]

    def __init__(self) -> None:
        cast(Any, self)._wrap_(self)
        self.delete_hresult: int | None = None
        self.recycle_item_identifier: str | None = None
        self.saw_recycle_transfer_flag = False

    def PreDeleteItem(self, flags: int, _item: object) -> int:
        """Abort unless the Shell states the queued transfer is recycle-capable."""
        self.saw_recycle_transfer_flag = bool(flags & shellcon.TSF_DELETE_RECYCLE_IF_POSSIBLE)
        return _S_OK if self.saw_recycle_transfer_flag else _E_FAIL

    def PostDeleteItem(
        self,
        _flags: int,
        _item: object,
        hr_delete: int,
        newly_created: object | None,
    ) -> int:
        """Record success only when Shell returns an item now located in Recycle Bin."""
        self.delete_hresult = int(hr_delete)
        if newly_created is not None:
            display = cast(Any, newly_created).GetDisplayName(shellcon.SIGDN_DESKTOPABSOLUTEPARSING)
            self.recycle_item_identifier = str(display)
        return _S_OK

    # All unrelated methods deliberately do nothing because this adapter queues DeleteItem only.
    def StartOperations(self) -> int:
        return _S_OK

    def FinishOperations(self, _result: int) -> int:
        return _S_OK

    def PreRenameItem(self, *_args: object) -> int:
        return _E_FAIL

    def PostRenameItem(self, *_args: object) -> int:
        return _E_FAIL

    def PreMoveItem(self, *_args: object) -> int:
        return _E_FAIL

    def PostMoveItem(self, *_args: object) -> int:
        return _E_FAIL

    def PreCopyItem(self, *_args: object) -> int:
        return _E_FAIL

    def PostCopyItem(self, *_args: object) -> int:
        return _E_FAIL

    def PreNewItem(self, *_args: object) -> int:
        return _E_FAIL

    def PostNewItem(self, *_args: object) -> int:
        return _E_FAIL

    def UpdateProgress(self, _total: int, _so_far: int) -> int:
        return _S_OK

    def ResetTimer(self) -> int:
        return _S_OK

    def PauseTimer(self) -> int:
        return _S_OK

    def ResumeTimer(self) -> int:
        return _S_OK


class WindowsRecycleBinPlatform:
    """Use one STA IFileOperation per item and require positive recycle evidence."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows Recycle Bin operations are available only on Windows")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    def capability(self, path: Path) -> RecycleBinCapability:
        """Allow only writable fixed NTFS volumes with a queryable Recycle Bin."""
        try:
            root = self._volume_root(path)
            get_drive_type = cast(Any, self._kernel32.GetDriveTypeW)
            get_drive_type.argtypes = [ctypes.c_wchar_p]
            get_drive_type.restype = ctypes.c_uint
            drive_type = int(get_drive_type(str(root)))
            filesystem, serial, flags = self._volume_information(root)
            hotplug = self._is_hotplug_or_unknown(root)
            query_ok = self._query_recycle_bin(root)
            available = (
                drive_type == _DRIVE_FIXED
                and filesystem.casefold() == "ntfs"
                and not flags & _FILE_READ_ONLY_VOLUME
                and hotplug is False
                and query_ok
            )
            reason = None
            if not available:
                reason = (
                    "Recycle Bin requires a local, non-hotplug, writable fixed NTFS volume "
                    "whose Shell Recycle Bin can be queried"
                )
            return RecycleBinCapability(
                available=available,
                volume_root=root,
                filesystem=filesystem,
                volume_serial=serial,
                fixed_drive=drive_type == _DRIVE_FIXED,
                read_only=bool(flags & _FILE_READ_ONLY_VOLUME),
                hotplug=hotplug,
                recycle_bin_query_succeeded=query_ok,
                reason=reason,
            )
        except OSError as exc:
            return RecycleBinCapability(available=False, reason=str(exc))

    def recycle(self, path: Path) -> RecycleBinResult:
        """Queue exactly one Shell recycle operation and reject every ambiguous outcome."""
        capability = self.capability(path)
        if not capability.available:
            raise RecycleBinPlatformError(capability.reason or "Recycle Bin is unavailable")
        if not path.is_absolute() or not path.exists():
            raise RecycleBinPlatformError(f"Recycle source is unavailable: {path}")
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        try:
            file_operation = cast(
                Any,
                pythoncom.CoCreateInstance(
                    shell.CLSID_FileOperation,
                    None,
                    pythoncom.CLSCTX_INPROC_SERVER,
                    shell.IID_IFileOperation,
                ),
            )
            flags = (
                shellcon.FOF_NOCONFIRMATION
                | shellcon.FOF_NOERRORUI
                | shellcon.FOF_SILENT
                | shellcon.FOF_NO_CONNECTED_ELEMENTS
                | shellcon.FOF_WANTNUKEWARNING
                | shellcon.FOFX_EARLYFAILURE
                | _FOFX_RECYCLEONDELETE
                | _FOFX_ADDUNDORECORD
            )
            file_operation.SetOperationFlags(flags)
            item = cast(Any, shell.SHCreateItemFromParsingName)(
                str(path), None, shell.IID_IShellItem
            )
            sink = _RecycleProgressSink()
            wrapped_sink = pythoncom.WrapObject(  # type: ignore[call-arg]
                sink, shell.IID_IFileOperationProgressSink
            )
            file_operation.DeleteItem(item, wrapped_sink)
            # pywin32 exposes successful HRESULT-returning COM methods as ``None``
            # on some generated wrappers. A non-None value is retained verbatim.
            perform_result = file_operation.PerformOperations()
            perform_hresult = _S_OK if perform_result is None else int(perform_result)
            aborted = bool(file_operation.GetAnyOperationsAborted())
        except pywintypes.com_error as exc:
            hresult = int(cast(Any, exc).hresult)
            raise RecycleBinPlatformError(
                hresult, f"Windows Recycle Bin operation failed (HRESULT {hresult:#x})", str(path)
            ) from exc
        finally:
            pythoncom.CoUninitialize()
        actual_hresult = sink.delete_hresult if sink.delete_hresult is not None else perform_hresult
        verified = (
            _hresult_succeeded(perform_hresult)
            and _hresult_succeeded(actual_hresult)
            and not aborted
            and sink.saw_recycle_transfer_flag
            and sink.recycle_item_identifier is not None
            and not path.exists()
        )
        if not verified:
            status = (
                RecycleVerificationStatus.FAILED
                if not _hresult_succeeded(perform_hresult) or not _hresult_succeeded(actual_hresult)
                else RecycleVerificationStatus.UNKNOWN
            )
            return RecycleBinResult(
                source=path,
                hresult=actual_hresult,
                aborted=aborted,
                recycled=False,
                recycle_item_identifier=sink.recycle_item_identifier,
                verification_status=status,
                message=(
                    "Windows did not provide complete evidence that the item entered Recycle Bin"
                ),
            )
        return RecycleBinResult(
            source=path,
            hresult=_S_OK,
            aborted=False,
            recycled=True,
            recycle_item_identifier=sink.recycle_item_identifier,
            verification_status=RecycleVerificationStatus.VERIFIED_RECYCLED,
            message="Windows Shell confirmed the item is now in Recycle Bin",
        )

    def _volume_root(self, path: Path) -> Path:
        """Ask Windows for the owning volume root instead of trusting path text."""
        buffer = ctypes.create_unicode_buffer(32_768)
        get_volume_path = cast(Any, self._kernel32.GetVolumePathNameW)
        get_volume_path.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
        get_volume_path.restype = ctypes.c_int
        if not get_volume_path(str(path), buffer, len(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
        return Path(buffer.value)

    def _volume_information(self, root: Path) -> tuple[str, int, int]:
        """Return filesystem, serial, and capability flags for one volume root."""
        filesystem = ctypes.create_unicode_buffer(256)
        serial = ctypes.c_ulong()
        max_component = ctypes.c_ulong()
        flags = ctypes.c_ulong()
        get_information = cast(Any, self._kernel32.GetVolumeInformationW)
        get_information.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.c_wchar_p,
            ctypes.c_ulong,
        ]
        get_information.restype = ctypes.c_int
        if not get_information(
            str(root),
            None,
            0,
            ctypes.byref(serial),
            ctypes.byref(max_component),
            ctypes.byref(flags),
            filesystem,
            len(filesystem),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return filesystem.value, int(serial.value), int(flags.value)

    def _query_recycle_bin(self, root: Path) -> bool:
        """Use the documented Shell query instead of opening protected $Recycle.Bin paths."""
        info = _SHQueryRBInfo()
        info.cbSize = ctypes.sizeof(info)
        query = cast(Any, self._shell32.SHQueryRecycleBinW)
        query.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(_SHQueryRBInfo)]
        query.restype = ctypes.c_long
        return int(query(str(root), ctypes.byref(info))) == _S_OK

    @staticmethod
    def _is_hotplug_or_unknown(root: Path) -> bool | None:
        """Return False only for the current system volume; unknown data fails closed."""
        system_root = Path(os.environ.get("SYSTEMROOT", "C:/Windows"))
        system_drive = system_root.drive.casefold()
        if root.drive.casefold() == system_drive:
            return False
        # A DRIVE_FIXED result can still be USB media. Until device hotplug IOCTL support is
        # independently tested, non-system volumes stay UNKNOWN and therefore unavailable.
        return None
