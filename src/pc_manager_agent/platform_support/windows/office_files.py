"""Office-only Win32 leases and fail-if-exists handle renames. No deletion or elevation."""

from __future__ import annotations

import ctypes
import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import win32con
import win32file
from pywintypes import error as WindowsApiError

from pc_manager_agent.domain.file_operations import FileObjectKind, FileState
from pc_manager_agent.domain.office_documents import (
    DocumentFormat,
    OfficeDocumentIdentity,
    OfficeError,
)
from pc_manager_agent.tools.manifest import CancellationToken


def _raw(path: Path) -> str:
    """Convert a previously authorized absolute local path to Unicode Win32 form."""
    if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
        raise OfficeError("DOCUMENT_PATH_BLOCKED")
    return "\\\\?\\" + str(path)


def office_main_is_elevated() -> bool:
    """Query only the current process token; unknown token state raises and blocks writing."""
    import win32api
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        return bool(win32security.GetTokenInformation(token, win32security.TokenElevation))
    finally:
        win32api.CloseHandle(token)


class OfficeFileLease:
    """Hold one current file open without sharing write/delete authority."""

    def __init__(self, handle: Any, path: Path) -> None:
        self.handle = handle
        self.path = path

    def read(self, maximum: int, cancellation: CancellationToken) -> bytes:
        """Read through the held handle with a strict size and cancellation bound."""
        win32file.SetFilePointer(self.handle, 0, win32con.FILE_BEGIN)
        chunks: list[bytes] = []
        total = 0
        while True:
            if cancellation.is_cancelled:
                raise OfficeError("DOCUMENT_CANCELLED")
            _status, data = win32file.ReadFile(self.handle, min(1024**2, maximum + 1 - total))
            if not data:
                break
            chunks.append(cast(bytes, data))
            total += len(data)
            if total > maximum:
                raise OfficeError("DOCUMENT_SIZE_LIMIT")
        return b"".join(chunks)

    def identity(self, data: bytes, format_: DocumentFormat) -> OfficeDocumentIdentity:
        """Observe IDs, size, times and attributes from the same held handle as content."""
        info = win32file.GetFileInformationByHandle(self.handle)
        attributes, created, _accessed, modified, volume, high, low, links, id_high, id_low = info
        if attributes & (0x400 | 0x1000 | 0x4) or attributes & 0x10:
            raise OfficeError("UNSUPPORTED_DOCUMENT_ATTRIBUTES")
        actual = win32file.GetFinalPathNameByHandle(self.handle, 0)
        if os.path.normcase(actual) != os.path.normcase(_raw(self.path)):
            raise OfficeError("DOCUMENT_PATH_CHANGED")
        size = (high << 32) | low
        if size != len(data):
            raise OfficeError("DOCUMENT_CHANGED_DURING_READ")
        state = FileState(
            path=self.path,
            kind=FileObjectKind.FILE,
            volume_serial=volume,
            file_id=f"{id_high:08x}{id_low:08x}",
            size_bytes=size,
            created_ns=int(created.timestamp() * 1_000_000) * 1000,
            modified_ns=int(modified.timestamp() * 1_000_000) * 1000,
            attributes=attributes,
        )
        cloud_roots = tuple(
            Path(value)
            for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
            if (value := os.environ.get(key))
        )
        return OfficeDocumentIdentity(
            state=state,
            format=format_,
            sha256=hashlib.sha256(data).hexdigest(),
            security_digest=self.security_digest(),
            hard_links=links,
            readonly=bool(attributes & 1),
            cloud_sync=any(self.path.is_relative_to(root) for root in cloud_roots)
            or any(part.casefold() in {"dropbox", "google drive"} for part in self.path.parts),
        )

    def write_new(self, data: bytes) -> None:
        """Fill a newly and exclusively created output, then flush it to the filesystem."""
        _status, written = win32file.WriteFile(self.handle, data)
        if written != len(data):
            raise OfficeError("DOCUMENT_SHORT_WRITE")
        win32file.FlushFileBuffers(self.handle)

    def require_replaceable(self, identity: OfficeDocumentIdentity) -> None:
        """Allow only ordinary single-stream NTFS files; never lose links or special metadata."""
        import win32api

        if identity.hard_links != 1 or identity.readonly:
            raise OfficeError("DOCUMENT_HARDLINK_OR_READONLY")
        # Encrypted, compressed, sparse, offline and recall-on-access require a
        # separate preservation design. A user confirmation cannot override this.
        if identity.state.attributes & (0x4000 | 0x800 | 0x200 | 0x1000 | 0x40000 | 0x400000):
            raise OfficeError("DOCUMENT_SPECIAL_STORAGE_BLOCKED")
        try:
            if win32api.GetVolumeInformation(self.path.anchor)[4].casefold() != "ntfs":
                raise OfficeError("DOCUMENT_WRITE_REQUIRES_NTFS")
            if any(stream[1] != "::$DATA" for stream in win32file.FindStreams(_raw(self.path))):
                raise OfficeError("DOCUMENT_EXTRA_STREAMS_BLOCKED")
        except WindowsApiError as exc:
            raise OfficeError("DOCUMENT_STORAGE_EVIDENCE_UNAVAILABLE") from exc

    def security_digest(self) -> str:
        """Bind owner, group and DACL; custom permissions are never silently replaced."""
        import win32security

        information = (
            win32security.OWNER_SECURITY_INFORMATION
            | win32security.GROUP_SECURITY_INFORMATION
            | win32security.DACL_SECURITY_INFORMATION
        )
        descriptor = win32security.GetKernelObjectSecurity(self.handle, information)
        sddl = win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(
            descriptor, win32security.SDDL_REVISION_1, information
        )
        return hashlib.sha256(sddl.encode()).hexdigest()

    def rename_absent(self, destination: Path) -> None:
        """Rename this exact open object; an occupied target is always an error."""

        class RenameInfo(ctypes.Structure):
            _fields_ = [
                ("replace", ctypes.c_ubyte),
                ("root", ctypes.c_void_p),
                ("length", ctypes.c_ulong),
                ("name", ctypes.c_wchar * 1),
            ]

        encoded = _raw(destination).encode("utf-16-le")
        buffer = ctypes.create_string_buffer(RenameInfo.name.offset + len(encoded) + 2)
        info = RenameInfo.from_buffer(buffer)
        info.replace = 0
        info.root = None
        info.length = len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.name.offset, encoded, len(encoded))
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        rename = cast(Any, kernel.SetFileInformationByHandle)
        rename.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
        rename.restype = ctypes.c_int
        if not rename(int(self.handle), 3, buffer, len(buffer)):
            raise OfficeError("DOCUMENT_COMMIT_CONFLICT_OR_DENIED")
        self.path = destination


class WindowsOfficeFiles:
    """Acquire directory pins and file leases for exact authorized paths only."""

    @contextmanager
    def pin_parents(self, path: Path) -> Iterator[None]:
        """Keep every parent non-reparse and non-renamable until the operation ends."""
        handles: list[Any] = []
        try:
            for parent in reversed(path.parents):
                handle = win32file.CreateFile(
                    _raw(parent),
                    0x0080,
                    win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
                    None,
                    win32con.OPEN_EXISTING,
                    0x02000000 | 0x00200000,
                    None,
                )
                handles.append(handle)
                info = win32file.GetFileInformationByHandle(int(handle))
                if not info[0] & 0x10 or info[0] & 0x400:
                    raise OfficeError("DOCUMENT_PARENT_REDIRECTED")
            yield
        except (OSError, WindowsApiError) as exc:
            raise OfficeError("DOCUMENT_PARENT_UNAVAILABLE") from exc
        finally:
            for handle in reversed(handles):
                handle.Close()

    @contextmanager
    def open(
        self, path: Path, *, mutable: bool = False, create: bool = False
    ) -> Iterator[OfficeFileLease]:
        """Open/create a non-redirected file; existing writers cause a safe denial."""
        access = win32con.GENERIC_READ
        if mutable or create:
            access |= win32con.DELETE
        if create:
            access |= win32con.GENERIC_WRITE
        try:
            handle = win32file.CreateFile(
                _raw(path),
                access,
                win32con.FILE_SHARE_READ,
                None,
                win32con.CREATE_NEW if create else win32con.OPEN_EXISTING,
                0x00200000 | 0x80,
                None,
            )
        except (OSError, WindowsApiError) as exc:
            raise OfficeError("DOCUMENT_IN_USE_OR_UNAVAILABLE") from exc
        try:
            info = win32file.GetFileInformationByHandle(int(handle))
            if info[0] & (0x400 | 0x1000 | 0x4 | 0x10) or info[7] != 1:
                raise OfficeError("DOCUMENT_REPARSE_HARDLINK_OR_SPECIAL_BLOCKED")
            yield OfficeFileLease(handle, path)
        finally:
            handle.Close()
