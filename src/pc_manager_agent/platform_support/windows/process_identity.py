"""Windows token, process, and executable identity evidence for Stage 4X2."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, cast

import psutil
import pywintypes
import win32api
import win32con
import win32security

from pc_manager_agent.domain.elevated_broker import (
    BrokerBinaryIdentity,
    SignatureStatus,
    WindowsProcessIdentity,
)

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_HASH_CHUNK_BYTES = 1024 * 1024


class WindowsIdentityError(RuntimeError):
    """Raised when required OS identity evidence is unavailable or inconsistent."""


class WindowsBrokerBinaryInspector:
    """Inspect one literal Broker path without resolving an executable through PATH."""

    def inspect(self, path: Path) -> BrokerBinaryIdentity:
        """Return stable path, file, hash, version, signature, and manifest evidence."""
        if not path.is_absolute():
            raise WindowsIdentityError("Broker executable path must be absolute")
        resolved = path.resolve(strict=True)
        if resolved != path.resolve(strict=False) or resolved.suffix.casefold() != ".exe":
            raise WindowsIdentityError("Broker executable path is not one exact local EXE")
        stat = resolved.lstat()
        attributes = int(getattr(stat, "st_file_attributes", 0))
        is_reparse = bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)
        if is_reparse or not resolved.is_file():
            raise WindowsIdentityError("Broker executable cannot be a reparse point")
        manifest = resolved.with_suffix(resolved.suffix + ".manifest")
        if not _manifest_is_as_invoker(manifest):
            raise WindowsIdentityError("Broker manifest must explicitly declare asInvoker")
        digest = sha256_file(resolved)
        signature = (
            SignatureStatus.VALID if authenticode_valid(resolved) else SignatureStatus.UNSIGNED
        )
        return BrokerBinaryIdentity(
            path_hash=_path_hash(resolved),
            file_id=f"{stat.st_dev:x}:{stat.st_ino:x}",
            sha256=digest,
            size_bytes=stat.st_size,
            product_version=_product_version(resolved),
            signature_status=signature,
            signer_fingerprint=None,
            trusted_location=_is_trusted_install_location(resolved),
            reparse_point=False,
        )


def capture_current_process_identity() -> WindowsProcessIdentity:
    """Capture the current process identity directly from its Windows access token."""
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        user = win32security.GetTokenInformation(token, win32security.TokenUser)
        sid = str(win32security.ConvertSidToStringSid(user[0]))
        session_id = int(win32security.GetTokenInformation(token, win32security.TokenSessionId))
        elevated = bool(win32security.GetTokenInformation(token, win32security.TokenElevation))
        integrity = win32security.GetTokenInformation(token, win32security.TokenIntegrityLevel)
        integrity_label = _integrity_label(cast(Any, integrity)[0])
    except (pywintypes.error, TypeError, ValueError) as exc:
        raise WindowsIdentityError("Current Windows token identity is unavailable") from exc
    finally:
        win32api.CloseHandle(token)
    return _process_identity(
        os.getpid(),
        sid=sid,
        session_id=session_id,
        elevated=elevated,
        integrity_level=integrity_label,
    )


def capture_impersonated_pipe_client_identity(
    pipe_handle: int,
    *,
    process_id: int,
    session_id: int,
) -> WindowsProcessIdentity:
    """Read the actual pipe client's token after successful server-side impersonation."""
    impersonated = False
    token: int | None = None
    try:
        # pywin32 raises on a false Win32 return value and returns ``None`` on success.
        win32security.ImpersonateNamedPipeClient(pipe_handle)
        impersonated = True
        token = cast(
            int,
            win32security.OpenThreadToken(
                win32api.GetCurrentThread(),
                win32con.TOKEN_QUERY,
                True,
            ),
        )
        user = win32security.GetTokenInformation(token, win32security.TokenUser)
        sid = str(win32security.ConvertSidToStringSid(user[0]))
        token_session = int(win32security.GetTokenInformation(token, win32security.TokenSessionId))
        elevated = bool(win32security.GetTokenInformation(token, win32security.TokenElevation))
        integrity = win32security.GetTokenInformation(token, win32security.TokenIntegrityLevel)
        integrity_label = _integrity_label(cast(Any, integrity)[0])
        if token_session != session_id:
            raise WindowsIdentityError("Pipe session and client token session differ")
        return _process_identity(
            process_id,
            sid=sid,
            session_id=session_id,
            elevated=elevated,
            integrity_level=integrity_label,
        )
    except (pywintypes.error, psutil.Error, TypeError, ValueError) as exc:
        if isinstance(exc, WindowsIdentityError):
            raise
        raise WindowsIdentityError("Named-pipe client identity is unavailable") from exc
    finally:
        if token is not None:
            win32api.CloseHandle(token)
        if impersonated:
            win32security.RevertToSelf()


def sha256_file(path: Path) -> str:
    """Hash one already validated ordinary file using a bounded read buffer."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def authenticode_valid(path: Path) -> bool:
    """Return an offline Authenticode chain conclusion using the existing SCM verifier."""
    # Stage 4C1 already centralizes the reviewed WinVerifyTrust structure. Importing here
    # avoids introducing a second subtly different ctypes trust implementation.
    from pc_manager_agent.platform_support.windows.service_control import (
        _authenticode_signature_valid,
    )

    return _authenticode_signature_valid(path)


def _process_identity(
    process_id: int,
    *,
    sid: str,
    session_id: int,
    elevated: bool,
    integrity_level: str,
) -> WindowsProcessIdentity:
    process = psutil.Process(process_id)
    image = Path(process.exe()).resolve(strict=True)
    return WindowsProcessIdentity(
        user_sid=sid,
        session_id=session_id,
        process_id=process_id,
        process_creation_time_ns=max(1, int(process.create_time() * 1_000_000_000)),
        image_path_hash=_path_hash(image),
        image_sha256=sha256_file(image),
        product_version=_product_version(image),
        elevated=elevated,
        integrity_level=integrity_level,
    )


def _path_hash(path: Path) -> str:
    normalized = str(path.resolve(strict=False)).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _product_version(path: Path) -> str | None:
    try:
        info = win32api.GetFileVersionInfo(str(path), "\\")
        ms = int(info["FileVersionMS"])
        ls = int(info["FileVersionLS"])
    except (OSError, KeyError, TypeError, ValueError, pywintypes.error):
        return None
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def _integrity_label(sid: Any) -> str:
    text = str(win32security.ConvertSidToStringSid(sid))
    try:
        rid = int(text.rsplit("-", 1)[1])
    except (IndexError, ValueError) as exc:
        raise WindowsIdentityError("Windows integrity SID is malformed") from exc
    if rid >= 0x4000:
        return "SYSTEM"
    if rid >= 0x3000:
        return "HIGH"
    if rid >= 0x2000:
        return "MEDIUM"
    if rid >= 0x1000:
        return "LOW"
    return "UNTRUSTED"


def _manifest_is_as_invoker(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    compact = "".join(text.split()).casefold()
    return 'requestedexecutionlevellevel="asinvoker"uiaccess="false"' in compact


def _is_trusted_install_location(path: Path) -> bool:
    roots = tuple(
        Path(value).resolve(strict=False)
        for name in ("ProgramFiles", "ProgramW6432")
        if (value := os.getenv(name))
    )
    return any(path.is_relative_to(root) for root in roots)
