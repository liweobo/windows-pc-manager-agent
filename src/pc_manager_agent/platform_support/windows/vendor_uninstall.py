"""Windows inspection and one-shot launch adapter for interactive Vendor uninstallers."""

from __future__ import annotations

import ctypes
import hashlib
import os

# Subprocess is confined to one validated absolute executable and exact argv vector.
import subprocess  # nosec B404
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import psutil

from pc_manager_agent.domain.vendor_uninstall import (
    ValidatedVendorUninstallAction,
    VendorExecutableFileIdentity,
    VendorExecutableObservation,
    VendorInstallLocationRelation,
    VendorProcessExecutionResult,
    VendorProcessResultCategory,
)
from pc_manager_agent.platform_support.windows.authenticode import WindowsAuthenticodeVerifier
from pc_manager_agent.platform_support.windows.file_operations import WindowsFileOperationPlatform
from pc_manager_agent.safety.vendor_executable_trust import conservative_publisher_match
from pc_manager_agent.tools.manifest import CancellationToken

_DRIVE_FIXED = 3
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
_ERROR_ELEVATION_REQUIRED = 740
_MAX_HASH_BYTES = 512 * 1024 * 1024
_SAFE_ENVIRONMENT_KEYS = frozenset(
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
_SECRET_FRAGMENTS = ("TOKEN", "SECRET", "PASSWORD", "COOKIE", "API_KEY", "CREDENTIAL")


class _PolledProcess(Protocol):
    pid: int

    def poll(self) -> int | None:
        """Return an exit code, or None while the direct child is running."""
        ...


class WindowsVendorExecutablePlatform:
    """Capture path, file, hash, and offline signature evidence without execution."""

    def __init__(
        self,
        *,
        authenticode: WindowsAuthenticodeVerifier | None = None,
        file_platform: WindowsFileOperationPlatform | None = None,
        environ: Mapping[str, str] | None = None,
        max_hash_bytes: int = _MAX_HASH_BYTES,
    ) -> None:
        if os.name != "nt" and file_platform is None:
            raise OSError("Vendor executable inspection is available only on Windows")
        if max_hash_bytes <= 0:
            raise ValueError("Vendor executable hash limit must be positive")
        self._authenticode = authenticode or WindowsAuthenticodeVerifier()
        self._files = file_platform or WindowsFileOperationPlatform()
        self._environ = dict(environ or os.environ)
        self._max_hash_bytes = max_hash_bytes

    def inspect(
        self,
        executable: Path,
        install_location: Path,
        publisher: str,
    ) -> VendorExecutableObservation:
        """Resolve and inspect one direct executable with before/after identity checks."""
        if not executable.is_absolute() or not install_location.is_absolute():
            raise PermissionError("Vendor executable and install location must be absolute")
        # Check the registry-supplied spelling before resolve() can erase evidence of a junction.
        # This keeps inspection from following an unapproved reparse target merely to classify it.
        if not _reparse_free(executable) or not _reparse_free(install_location):
            raise PermissionError("Vendor executable or install location contains a reparse point")
        canonical = executable.resolve(strict=True)
        install_root = install_location.resolve(strict=True)
        if canonical.suffix.casefold() != ".exe" or not canonical.is_file():
            raise PermissionError("Vendor executable must be an existing regular .exe")
        reparse_free = _reparse_free(canonical) and _reparse_free(install_root)
        relation = (
            VendorInstallLocationRelation.INSIDE_INSTALL_LOCATION
            if _is_within(canonical, install_root) and canonical != install_root
            else VendorInstallLocationRelation.OUTSIDE_INSTALL_LOCATION
        )
        local_fixed = _is_local_fixed_volume(canonical)
        blocked_location = _is_blocked_location(canonical, self._environ)
        before = self._files.inspect(canonical)
        if before.size_bytes > self._max_hash_bytes:
            raise PermissionError("Vendor executable exceeds the bounded SHA-256 limit")
        sha256 = _sha256(canonical)
        signature = self._authenticode.verify(canonical)
        after = self._files.inspect(canonical)
        if before != after:
            raise PermissionError("Vendor executable changed during trust inspection")
        if not _reparse_free(executable) or not _reparse_free(install_location):
            raise PermissionError("Vendor path became a reparse point during trust inspection")
        identity = VendorExecutableFileIdentity(
            executable_path=canonical,
            volume_serial=before.volume_serial,
            file_id=before.file_id,
            size_bytes=before.size_bytes,
            created_ns=before.created_ns,
            modified_ns=before.modified_ns,
            attributes=before.attributes,
            sha256=sha256,
        )
        signer = signature.signer_organization or signature.signer_subject
        return VendorExecutableObservation(
            file_identity=identity,
            local_fixed_volume=local_fixed,
            reparse_free=reparse_free,
            blocked_location=blocked_location,
            install_location_relation=relation,
            authenticode=signature,
            publisher_match=conservative_publisher_match(publisher, signer),
        )


class WindowsVendorUninstallPlatform:
    """Launch one fully validated local executable with no shell, elevation, or process control."""

    def __init__(
        self,
        *,
        popen_factory: Callable[..., _PolledProcess] | None = None,
        environ: Mapping[str, str] | None = None,
        poll_interval_seconds: float = 0.25,
        long_running_seconds: float = 900.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if poll_interval_seconds <= 0 or long_running_seconds <= 0:
            raise ValueError("Vendor monitor intervals must be positive")
        self._popen = popen_factory or subprocess.Popen
        self._environment = sanitized_vendor_environment(environ or os.environ)
        self._poll_interval = poll_interval_seconds
        self._long_running = long_running_seconds
        self._monotonic = monotonic
        self._sleep = sleeper

    def uninstall(
        self,
        action: ValidatedVendorUninstallAction,
        cancellation: CancellationToken,
    ) -> VendorProcessExecutionResult:
        """Launch exact immutable argv once and stop monitoring without terminating anything."""
        if cancellation.cancellation_requested():
            return VendorProcessExecutionResult(
                category=VendorProcessResultCategory.CANCELLED_BEFORE_LAUNCH,
                launched=False,
                cancellation_requested_before_launch=True,
            )
        executable = action.vendor_identity.executable.file_identity.executable_path
        arguments = action.vendor_identity.arguments
        if (
            not executable.is_absolute()
            or executable.suffix.casefold() != ".exe"
            or str(executable).startswith(("\\\\", "//"))
        ):
            raise PermissionError("Validated Vendor action contains an invalid executable path")
        if not _matches_identity(executable, action.vendor_identity.executable.file_identity):
            return VendorProcessExecutionResult(
                category=VendorProcessResultCategory.LAUNCH_FAILED,
                launched=False,
                error_type="ExecutableIdentityChanged",
            )
        started_at = datetime.now(UTC)
        started = self._monotonic()
        try:
            process = self._popen(
                [str(executable), *arguments],
                executable=str(executable),
                shell=False,
                cwd=str(executable.parent),
                env=self._environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError as exc:
            category = (
                VendorProcessResultCategory.VENDOR_REQUESTED_ELEVATION
                if getattr(exc, "winerror", None) == _ERROR_ELEVATION_REQUIRED
                else VendorProcessResultCategory.LAUNCH_FAILED
            )
            return VendorProcessExecutionResult(
                category=category,
                launched=False,
                started_at=started_at,
                duration_ms=max(0, round((self._monotonic() - started) * 1000)),
                error_type=type(exc).__name__,
            )
        tracked_children: set[int] = set()
        direct_exit: int | None = None
        while True:
            tracked_children.update(_child_processes(process.pid))
            direct_exit = process.poll()
            children_running = any(psutil.pid_exists(pid) for pid in tracked_children)
            elapsed = self._monotonic() - started
            if direct_exit is not None and not children_running:
                break
            if cancellation.cancellation_requested():
                return VendorProcessExecutionResult(
                    category=VendorProcessResultCategory.STOPPED_MONITORING,
                    process_id=process.pid,
                    launched=True,
                    monitoring_stopped_after_launch=True,
                    tracked_child_count=len(tracked_children),
                    started_at=started_at,
                    duration_ms=max(0, round(elapsed * 1000)),
                )
            if elapsed >= self._long_running:
                return VendorProcessExecutionResult(
                    category=VendorProcessResultCategory.MONITORING_DETACHED,
                    process_id=process.pid,
                    launched=True,
                    long_running_observed=True,
                    tracked_child_count=len(tracked_children),
                    started_at=started_at,
                    duration_ms=max(0, round(elapsed * 1000)),
                )
            self._sleep(self._poll_interval)
        exit_code = int(direct_exit or 0)
        return VendorProcessExecutionResult(
            category=(
                VendorProcessResultCategory.PROCESS_EXITED_ZERO
                if exit_code == 0
                else VendorProcessResultCategory.PROCESS_EXITED_NONZERO
            ),
            exit_code=exit_code,
            process_id=process.pid,
            launched=True,
            tracked_child_count=len(tracked_children),
            started_at=started_at,
            duration_ms=max(0, round((self._monotonic() - started) * 1000)),
        )


def sanitized_vendor_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Build a small child environment and drop every likely secret-bearing variable."""
    result: dict[str, str] = {}
    for key, value in environment.items():
        upper = key.upper()
        if upper not in _SAFE_ENVIRONMENT_KEYS or any(part in upper for part in _SECRET_FRAGMENTS):
            continue
        if "\x00" in key or "\x00" in value:
            continue
        result[key] = value
    return result


def _matches_identity(path: Path, expected: VendorExecutableFileIdentity) -> bool:
    """Recheck content and simple metadata at the adapter boundary before CreateProcess."""
    try:
        metadata = path.stat(follow_symlinks=False)
        if (
            metadata.st_size != expected.size_bytes
            or metadata.st_ctime_ns != expected.created_ns
            or metadata.st_mtime_ns != expected.modified_ns
        ):
            return False
        return _sha256(path) == expected.sha256
    except OSError:
        return False


def _sha256(path: Path) -> str:
    """Hash one bounded local executable without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _child_processes(pid: int) -> set[int]:
    """Observe currently related descendants without signalling or controlling them."""
    try:
        return {child.pid for child in psutil.Process(pid).children(recursive=True)}
    except (psutil.Error, OSError):
        return set()


def _is_local_fixed_volume(path: Path) -> bool:
    """Require GetDriveTypeW to identify the explicit drive as local fixed storage."""
    if os.name != "nt" or not path.anchor:
        return False
    function: Any = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
    function.argtypes = [ctypes.c_wchar_p]
    function.restype = ctypes.c_uint
    return int(function(path.anchor)) == _DRIVE_FIXED


def _reparse_free(path: Path) -> bool:
    """Reject a reparse point at any existing component from drive root to object."""
    if os.name != "nt":
        return False
    function: Any = ctypes.WinDLL("kernel32", use_last_error=True).GetFileAttributesW
    function.argtypes = [ctypes.c_wchar_p]
    function.restype = ctypes.c_ulong
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        attributes = int(function(str(current)))
        if attributes == _INVALID_FILE_ATTRIBUTES or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            return False
    return True


def _is_blocked_location(path: Path, environment: Mapping[str, str]) -> bool:
    """Block executable paths under temporary, Downloads, and common cache directories."""
    roots: list[Path] = []
    for key in ("TEMP", "TMP"):
        if value := environment.get(key):
            roots.append(Path(value).resolve(strict=False))
    if user_profile := environment.get("USERPROFILE"):
        roots.append((Path(user_profile) / "Downloads").resolve(strict=False))
    if local_app_data := environment.get("LOCALAPPDATA"):
        local = Path(local_app_data)
        roots.extend(
            (
                (local / "Temp").resolve(strict=False),
                (local / "Microsoft" / "Windows" / "INetCache").resolve(strict=False),
            )
        )
    return any(_is_within(path, root) for root in roots)


def _is_within(path: Path, root: Path) -> bool:
    """Compare normalized case-insensitive Windows paths without prefix string matching."""
    normalized_path = Path(os.path.normcase(str(path)))
    normalized_root = Path(os.path.normcase(str(root)))
    try:
        normalized_path.relative_to(normalized_root)
    except ValueError:
        return False
    return True
