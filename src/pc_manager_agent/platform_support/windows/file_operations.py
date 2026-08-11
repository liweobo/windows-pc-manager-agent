"""Windows file identity and fail-if-exists mutation primitives for Stage 2A."""

from __future__ import annotations

import ctypes
import os
import stat
from pathlib import Path
from typing import Any, cast

from pc_manager_agent.domain.file_operations import FileObjectKind, FileState

_FILE_READ_ATTRIBUTES = 0x0080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_ATTRIBUTE_SYSTEM = 0x00000004
_FILE_ATTRIBUTE_OFFLINE = 0x00001000
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
_FILE_ID_INFO_CLASS = 18
_MOVEFILE_WRITE_THROUGH = 0x00000008
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_ACCESS_DENIED = 5
_ERROR_SHARING_VIOLATION = 32
_ERROR_FILE_EXISTS = 80
_ERROR_ALREADY_EXISTS = 183
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class WindowsFileOperationError(OSError):
    """Raised when a checked Win32 file operation fails."""


class _FileId128(ctypes.Structure):
    _fields_ = [("identifier", ctypes.c_ubyte * 16)]


class _FileIdInfo(ctypes.Structure):
    _fields_ = [
        ("volume_serial_number", ctypes.c_ulonglong),
        ("file_id", _FileId128),
    ]


def _extended_path(path: Path) -> str:
    """Convert an already validated local absolute path to Win32 long-path form."""
    value = os.fspath(path)
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith(("\\\\", "//")):
        raise ValueError("UNC and device paths are not accepted by Stage 2A")
    if not path.is_absolute():
        raise ValueError("Win32 file operations require absolute paths")
    return f"\\\\?\\{value}"


def _raise_windows_error(action: str, path: Path, error_code: int | None = None) -> None:
    """Raise a stable Python exception without exposing unrelated process state."""
    code = ctypes.get_last_error() if error_code is None else error_code
    message = f"{action} failed for {path} (Windows error {code})"
    if code in {_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND}:
        raise FileNotFoundError(code, message, os.fspath(path))
    if code in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}:
        raise FileExistsError(code, message, os.fspath(path))
    if code in {_ERROR_ACCESS_DENIED, _ERROR_SHARING_VIOLATION}:
        raise PermissionError(code, message, os.fspath(path))
    raise WindowsFileOperationError(code, message, os.fspath(path))


class WindowsFileOperationPlatform:
    """Use Unicode Win32 APIs with no copy, replacement, delayed, or recursive flags."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows file operations are available only on Windows")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def inspect(self, path: Path) -> FileState:
        """Return handle-based volume/file identity plus mutation-relevant metadata."""
        canonical = Path(os.path.abspath(os.path.normpath(os.fspath(path))))
        raw = _extended_path(canonical)
        get_attributes = cast(Any, self._kernel32.GetFileAttributesW)
        get_attributes.argtypes = [ctypes.c_wchar_p]
        get_attributes.restype = ctypes.c_ulong
        attributes = int(get_attributes(raw))
        if attributes == _INVALID_FILE_ATTRIBUTES:
            _raise_windows_error("Inspect attributes", canonical)
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise PermissionError(f"Reparse points are not valid operation objects: {canonical}")
        if attributes & _FILE_ATTRIBUTE_SYSTEM:
            raise PermissionError(f"System objects are unavailable in Stage 2A: {canonical}")
        if attributes & _FILE_ATTRIBUTE_OFFLINE:
            raise PermissionError(f"Offline placeholders are unavailable in Stage 2A: {canonical}")

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
            raw,
            _FILE_READ_ATTRIBUTES,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == _INVALID_HANDLE_VALUE:
            _raise_windows_error("Open identity handle", canonical)
        try:
            identity = _FileIdInfo()
            get_info = cast(Any, self._kernel32.GetFileInformationByHandleEx)
            get_info.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
            get_info.restype = ctypes.c_int
            success = get_info(
                handle,
                _FILE_ID_INFO_CLASS,
                ctypes.byref(identity),
                ctypes.sizeof(identity),
            )
            if not success:
                _raise_windows_error("Read file identity", canonical)
        finally:
            close_handle = cast(Any, self._kernel32.CloseHandle)
            close_handle.argtypes = [ctypes.c_void_p]
            close_handle.restype = ctypes.c_int
            close_handle(handle)

        metadata = os.stat(canonical, follow_symlinks=False)
        kind = (
            FileObjectKind.DIRECTORY
            if attributes & _FILE_ATTRIBUTE_DIRECTORY
            else FileObjectKind.FILE
        )
        if kind is FileObjectKind.FILE and not stat.S_ISREG(metadata.st_mode):
            raise PermissionError(f"Only regular files and directories are supported: {canonical}")
        file_id = bytes(identity.file_id.identifier).hex()
        if not file_id or int(file_id, 16) == 0:
            raise PermissionError(f"A stable file identifier is unavailable: {canonical}")
        return FileState(
            path=canonical,
            kind=kind,
            volume_serial=int(identity.volume_serial_number),
            file_id=file_id,
            size_bytes=metadata.st_size if kind is FileObjectKind.FILE else 0,
            created_ns=metadata.st_ctime_ns,
            modified_ns=metadata.st_mtime_ns,
            attributes=attributes,
        )

    def move_same_volume(self, source: Path, destination: Path) -> None:
        """Move with no cross-volume-copy or replacement flags."""
        move_file = cast(Any, self._kernel32.MoveFileExW)
        move_file.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
        move_file.restype = ctypes.c_int
        success = move_file(
            _extended_path(source),
            _extended_path(destination),
            _MOVEFILE_WRITE_THROUGH,
        )
        if not success:
            _raise_windows_error("Move", source)

    def create_directory(self, destination: Path) -> None:
        """Create one leaf directory and fail closed on collision or missing parent."""
        create_directory = cast(Any, self._kernel32.CreateDirectoryW)
        create_directory.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
        create_directory.restype = ctypes.c_int
        if not create_directory(_extended_path(destination), None):
            _raise_windows_error("Create directory", destination)

    def remove_empty_directory(self, path: Path) -> None:
        """Remove one empty directory after the caller has revalidated identity and type."""
        remove_directory = cast(Any, self._kernel32.RemoveDirectoryW)
        remove_directory.argtypes = [ctypes.c_wchar_p]
        remove_directory.restype = ctypes.c_int
        if not remove_directory(_extended_path(path)):
            _raise_windows_error("Remove empty directory", path)
