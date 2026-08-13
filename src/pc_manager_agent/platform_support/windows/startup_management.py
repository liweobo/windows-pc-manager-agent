"""Narrow ordinary-user Windows startup inspection, disable, and restore adapter."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import os
import winreg
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pythoncom
import win32api
from win32com.shell import shell, shellcon

from pc_manager_agent.domain.startup_actions import (
    FolderStartupIdentity,
    RegistryStartupIdentity,
    StartupBackupPayload,
    StartupEntryStatus,
    StartupIdentity,
    StartupManagementMode,
    StartupObservation,
    StartupSource,
)
from pc_manager_agent.domain.startup_errors import (
    StartupConflictError,
    StartupIdentityChangedError,
)
from pc_manager_agent.platform_support.windows.file_operations import (
    WindowsFileOperationPlatform,
)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_ONCE_KEY = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
_APPROVED_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
_APPROVED_FOLDER_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"
)
_REGISTRY_SOURCES: tuple[tuple[StartupSource, int, str, str, str], ...] = (
    (StartupSource.HKCU_RUN, winreg.HKEY_CURRENT_USER, "HKCU", _RUN_KEY, "NATIVE"),
    (StartupSource.HKCU_RUN_ONCE, winreg.HKEY_CURRENT_USER, "HKCU", _RUN_ONCE_KEY, "NATIVE"),
    (StartupSource.HKLM_RUN, winreg.HKEY_LOCAL_MACHINE, "HKLM", _RUN_KEY, "64"),
    (StartupSource.HKLM_RUN, winreg.HKEY_LOCAL_MACHINE, "HKLM", _RUN_KEY, "32"),
    (StartupSource.HKLM_RUN_ONCE, winreg.HKEY_LOCAL_MACHINE, "HKLM", _RUN_ONCE_KEY, "64"),
    (StartupSource.HKLM_RUN_ONCE, winreg.HKEY_LOCAL_MACHINE, "HKLM", _RUN_ONCE_KEY, "32"),
)
_REG_SZ = winreg.REG_SZ
_REG_EXPAND_SZ = winreg.REG_EXPAND_SZ
_KEY_QUERY_VALUE = 0x0001
_KEY_SET_VALUE = 0x0002
_KEY_WOW64_32KEY = 0x0200
_KEY_WOW64_64KEY = 0x0100
_ERROR_SUCCESS = 0
_ERROR_FILE_NOT_FOUND = 2
_ERROR_ACCESS_DENIED = 5
_ERROR_MORE_DATA = 234


class WindowsStartupManagementError(OSError):
    """Raised when a checked Win32 startup operation fails closed."""


class WindowsStartupManagementPlatform:
    """Manage only HKCU Run and current-user ``.lnk`` Startup Folder entries."""

    def __init__(self, disabled_storage: Path) -> None:
        if os.name != "nt":
            raise OSError("Windows startup management is available only on Windows")
        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._ktmw32 = ctypes.WinDLL("ktmw32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        self._file_platform = WindowsFileOperationPlatform()
        self._user_startup = Path(
            shell.SHGetKnownFolderPath(shellcon.FOLDERID_Startup, 0, None)
        ).resolve(strict=False)
        self._common_startup = Path(
            shell.SHGetKnownFolderPath(shellcon.FOLDERID_CommonStartup, 0, None)
        ).resolve(strict=False)
        self._disabled_storage = disabled_storage.resolve(strict=False)
        self._disabled_storage.mkdir(parents=True, exist_ok=True)

    def list_entries(self, max_items: int = 5_000) -> tuple[StartupObservation, ...]:
        """Read a bounded inventory; unsupported sources remain visible but read-only."""
        if max_items < 1:
            raise ValueError("Startup inventory limit must be positive")
        entries: list[StartupObservation] = []
        for source, hive, hive_name, key_path, registry_view in _REGISTRY_SOURCES:
            entries.extend(
                self._list_registry_source(
                    source,
                    hive,
                    hive_name,
                    key_path,
                    registry_view,
                    max_items - len(entries),
                )
            )
            if len(entries) >= max_items:
                break
        if len(entries) < max_items:
            entries.extend(
                self._list_folder_source(
                    StartupSource.USER_STARTUP_FOLDER,
                    self._user_startup,
                    max_items - len(entries),
                )
            )
        if len(entries) < max_items:
            entries.extend(
                self._list_folder_source(
                    StartupSource.COMMON_STARTUP_FOLDER,
                    self._common_startup,
                    max_items - len(entries),
                )
            )
        return tuple(sorted(entries[:max_items], key=lambda value: value.display_name.casefold()))

    def inspect(self, identity: StartupIdentity) -> StartupObservation | None:
        """Re-read one exact object and reject replacement under the same display name/path."""
        current = self._inspect_location(identity)
        if current is None:
            return None
        if current.identity.canonical_digest() != identity.canonical_digest():
            raise StartupIdentityChangedError()
        return current

    def capture_backup(
        self,
        identity: StartupIdentity,
        backup_id: UUID,
    ) -> StartupBackupPayload:
        """Capture exact bytes only after the source-specific identity still matches."""
        current = self.inspect(identity)
        if current is None:
            raise StartupIdentityChangedError("Startup entry disappeared before backup")
        if identity.source is StartupSource.HKCU_RUN:
            registry_detail = _require_registry(identity)
            value_type, data = self._read_raw_value(
                winreg.HKEY_CURRENT_USER,
                registry_detail.key_path,
                registry_detail.value_name,
                registry_detail.registry_view,
            )
            approval = self._read_optional_raw_value(
                winreg.HKEY_CURRENT_USER,
                _APPROVED_RUN_KEY,
                registry_detail.value_name,
                "NATIVE",
            )
            return StartupBackupPayload(
                original_identity=identity,
                source=identity.source,
                registry_value_data_b64=base64.b64encode(data).decode("ascii"),
                registry_value_type=value_type,
                approval_data_b64=(
                    base64.b64encode(approval[1]).decode("ascii") if approval else None
                ),
            )
        if identity.source is StartupSource.USER_STARTUP_FOLDER:
            folder_detail = _require_folder(identity)
            data = _read_bounded_file(folder_detail.shortcut_path)
            approval = self._read_optional_raw_value(
                winreg.HKEY_CURRENT_USER,
                _APPROVED_FOLDER_KEY,
                folder_detail.shortcut_path.name,
                "NATIVE",
            )
            destination = self._disabled_storage / f"{backup_id}.lnk"
            if destination.exists():
                raise StartupConflictError("Reserved disabled-storage path already exists")
            return StartupBackupPayload(
                original_identity=identity,
                source=identity.source,
                shortcut_data_b64=base64.b64encode(data).decode("ascii"),
                approval_data_b64=(
                    base64.b64encode(approval[1]).decode("ascii") if approval else None
                ),
                disabled_storage_path=destination,
            )
        raise PermissionError("This startup source is read-only in Stage 4B")

    def disable(self, payload: StartupBackupPayload) -> None:
        """Remove exact HKCU Run data transactionally or move one exact shell link."""
        current = self.inspect(payload.original_identity)
        if current is None:
            raise StartupIdentityChangedError("Startup entry is already absent")
        self._require_approval_unchanged(payload)
        if payload.source is StartupSource.HKCU_RUN:
            registry_detail = _require_registry(payload.original_identity)
            self._delete_registry_value_transacted(registry_detail)
            if self._inspect_location(payload.original_identity) is not None:
                raise WindowsStartupManagementError("HKCU Run value remained after commit")
            return
        if payload.source is StartupSource.USER_STARTUP_FOLDER:
            folder_detail = _require_folder(payload.original_identity)
            destination = _require_disabled_path(payload)
            if destination.exists():
                raise StartupConflictError("Disabled-storage destination is occupied")
            source_state = self._file_platform.inspect(folder_detail.shortcut_path)
            parent_state = self._file_platform.inspect(destination.parent)
            if source_state.volume_serial != parent_state.volume_serial:
                raise PermissionError("Startup link and disabled storage must be on one volume")
            self._file_platform.move_same_volume(folder_detail.shortcut_path, destination)
            if folder_detail.shortcut_path.exists() or not self.disabled_material_matches(payload):
                raise WindowsStartupManagementError("Startup link disable verification failed")
            return
        raise PermissionError("This startup source is read-only in Stage 4B")

    def restore(self, payload: StartupBackupPayload) -> None:
        """Restore exact bytes only if the active location remains empty and conflict-free."""
        if self._inspect_location(payload.original_identity) is not None:
            raise StartupConflictError()
        self._require_approval_unchanged(payload)
        if payload.source is StartupSource.HKCU_RUN:
            registry_detail = _require_registry(payload.original_identity)
            raw = base64.b64decode(payload.registry_value_data_b64 or "", validate=True)
            if payload.registry_value_type is None:
                raise WindowsStartupManagementError("Registry backup type is absent")
            self._set_registry_value_transacted(registry_detail, payload.registry_value_type, raw)
            restored = self._inspect_location(payload.original_identity)
            if restored is None:
                raise WindowsStartupManagementError("HKCU Run restore verification failed")
            return
        if payload.source is StartupSource.USER_STARTUP_FOLDER:
            folder_detail = _require_folder(payload.original_identity)
            source = _require_disabled_path(payload)
            if folder_detail.shortcut_path.exists():
                raise StartupConflictError()
            if not self.disabled_material_matches(payload):
                raise StartupIdentityChangedError("Disabled shortcut is absent or changed")
            source_state = self._file_platform.inspect(source)
            parent_state = self._file_platform.inspect(folder_detail.shortcut_path.parent)
            if source_state.volume_serial != parent_state.volume_serial:
                raise PermissionError("Startup link and original location must be on one volume")
            self._file_platform.move_same_volume(source, folder_detail.shortcut_path)
            restored = self._inspect_location(payload.original_identity)
            if restored is None:
                raise WindowsStartupManagementError("Startup link restore verification failed")
            return
        raise PermissionError("This startup source cannot be restored by Stage 4B")

    def disabled_material_matches(self, payload: StartupBackupPayload) -> bool:
        """Validate Agent storage by file identity and exact encrypted-backup digest."""
        if payload.source is StartupSource.HKCU_RUN:
            return self._inspect_location(payload.original_identity) is None
        if payload.source is not StartupSource.USER_STARTUP_FOLDER:
            return False
        destination = _require_disabled_path(payload)
        detail = _require_folder(payload.original_identity)
        try:
            state = self._file_platform.inspect(destination)
            data = _read_bounded_file(destination)
        except (OSError, PermissionError):
            return False
        expected_data = base64.b64decode(payload.shortcut_data_b64 or "", validate=True)
        return (
            state.volume_serial == detail.volume_serial
            and state.file_id.casefold() == detail.file_id.casefold()
            and hashlib.sha256(data).digest() == hashlib.sha256(expected_data).digest()
        )

    def _list_registry_source(
        self,
        source: StartupSource,
        hive: int,
        hive_name: str,
        key_path: str,
        registry_view: str,
        limit: int,
    ) -> list[StartupObservation]:
        if limit <= 0:
            return []
        access = winreg.KEY_READ | _view_flag(registry_view)
        results: list[StartupObservation] = []
        try:
            with winreg.OpenKey(hive, key_path, 0, access) as key:
                count = winreg.QueryInfoKey(key)[1]
                names = [winreg.EnumValue(key, index)[0] for index in range(count)]
        except FileNotFoundError:
            return []
        except OSError:
            return []
        for name in names[:limit]:
            try:
                value_type, data = self._read_raw_value(hive, key_path, name, registry_view)
                approval = self._approval_for_registry(source, name)
                results.append(
                    self._registry_observation(
                        source,
                        hive_name,
                        key_path,
                        registry_view,
                        name,
                        value_type,
                        data,
                        approval,
                    )
                )
            except (OSError, ValueError):
                results.append(
                    _unresolved_registry_observation(
                        source,
                        hive_name,
                        key_path,
                        registry_view,
                        name,
                    )
                )
        return results

    def _list_folder_source(
        self,
        source: StartupSource,
        folder: Path,
        limit: int,
    ) -> list[StartupObservation]:
        if limit <= 0 or not folder.is_dir():
            return []
        results: list[StartupObservation] = []
        try:
            children = tuple(folder.iterdir())
        except OSError:
            return []
        for child in children[:limit]:
            try:
                results.append(self._folder_observation(source, folder, child))
            except (OSError, ValueError, pythoncom.com_error):
                results.append(_unresolved_folder_observation(source, child))
        return results

    def _registry_observation(
        self,
        source: StartupSource,
        hive_name: str,
        key_path: str,
        registry_view: str,
        name: str,
        value_type: int,
        data: bytes,
        approval: tuple[int, bytes] | None,
    ) -> StartupObservation:
        command = _decode_registry_command(value_type, data)
        executable, argument_count = _resolve_command(command)
        digest = hashlib.sha256(data).hexdigest()
        approval_digest = hashlib.sha256(approval[1]).hexdigest() if approval else None
        status, evidence = _approval_status(approval)
        source_supported = source is StartupSource.HKCU_RUN and value_type in {
            _REG_SZ,
            _REG_EXPAND_SZ,
        }
        return StartupObservation(
            identity=StartupIdentity(
                source=source,
                registry=RegistryStartupIdentity(
                    hive=hive_name,
                    key_path=key_path,
                    value_name=name,
                    value_type=value_type,
                    value_data_digest=digest,
                    command_fingerprint=digest,
                    resolved_executable_path=executable,
                    approval_data_digest=approval_digest,
                    registry_view=registry_view,
                ),
            ),
            display_name=name,
            publisher=_publisher(executable),
            executable_path=executable,
            command_summary=f"{executable.name} ({argument_count} argument(s))",
            scope="CURRENT_USER" if hive_name == "HKCU" else "ALL_USERS",
            status=status,
            status_evidence=evidence,
            management_mode=(
                StartupManagementMode.DISABLE_SUPPORTED
                if source_supported and status is StartupEntryStatus.ENABLED
                else StartupManagementMode.READ_ONLY
            ),
        )

    def _folder_observation(
        self,
        source: StartupSource,
        folder: Path,
        child: Path,
    ) -> StartupObservation:
        canonical = child.resolve(strict=True)
        if canonical.parent != folder or child.suffix.casefold() != ".lnk":
            raise ValueError("Only direct .lnk Startup Folder children are supported")
        state = self._file_platform.inspect(child)
        data = _read_bounded_file(child)
        target, arguments, working_directory = _read_shell_link(child)
        executable = _resolve_executable_path(target)
        approval = self._read_optional_raw_value(
            winreg.HKEY_CURRENT_USER,
            _APPROVED_FOLDER_KEY,
            child.name,
            "NATIVE",
        )
        status, evidence = _approval_status(approval)
        identity = StartupIdentity(
            source=source,
            folder=FolderStartupIdentity(
                shortcut_path=child.resolve(strict=False),
                volume_serial=state.volume_serial,
                file_id=state.file_id,
                shortcut_digest=hashlib.sha256(data).hexdigest(),
                resolved_target=executable,
                arguments_fingerprint=hashlib.sha256(arguments.encode()).hexdigest(),
                working_directory_fingerprint=hashlib.sha256(
                    working_directory.encode()
                ).hexdigest(),
                approval_data_digest=(
                    hashlib.sha256(approval[1]).hexdigest() if approval else None
                ),
            ),
        )
        supported = source is StartupSource.USER_STARTUP_FOLDER
        return StartupObservation(
            identity=identity,
            display_name=child.stem,
            publisher=_publisher(executable),
            executable_path=executable,
            command_summary=f"{executable.name} (shortcut arguments withheld)",
            scope="CURRENT_USER" if supported else "ALL_USERS",
            status=status,
            status_evidence=evidence,
            management_mode=(
                StartupManagementMode.DISABLE_SUPPORTED
                if supported and status is StartupEntryStatus.ENABLED
                else StartupManagementMode.READ_ONLY
            ),
        )

    def _inspect_location(self, identity: StartupIdentity) -> StartupObservation | None:
        if identity.registry is not None:
            detail = identity.registry
            hive = winreg.HKEY_CURRENT_USER if detail.hive == "HKCU" else winreg.HKEY_LOCAL_MACHINE
            try:
                value_type, data = self._read_raw_value(
                    hive,
                    detail.key_path,
                    detail.value_name,
                    detail.registry_view,
                )
            except FileNotFoundError:
                return None
            approval = self._approval_for_registry(identity.source, detail.value_name)
            return self._registry_observation(
                identity.source,
                detail.hive,
                detail.key_path,
                detail.registry_view,
                detail.value_name,
                value_type,
                data,
                approval,
            )
        folder_detail = _require_folder(identity)
        if not folder_detail.shortcut_path.exists():
            return None
        folder = (
            self._user_startup
            if identity.source is StartupSource.USER_STARTUP_FOLDER
            else self._common_startup
        )
        return self._folder_observation(identity.source, folder, folder_detail.shortcut_path)

    def _approval_for_registry(
        self,
        source: StartupSource,
        name: str,
    ) -> tuple[int, bytes] | None:
        if source not in {StartupSource.HKCU_RUN, StartupSource.HKLM_RUN}:
            return None
        hive = (
            winreg.HKEY_CURRENT_USER
            if source is StartupSource.HKCU_RUN
            else winreg.HKEY_LOCAL_MACHINE
        )
        return self._read_optional_raw_value(hive, _APPROVED_RUN_KEY, name, "NATIVE")

    def _require_approval_unchanged(self, payload: StartupBackupPayload) -> None:
        identity = payload.original_identity
        expected = (
            identity.registry.approval_data_digest
            if identity.registry is not None
            else _require_folder(identity).approval_data_digest
        )
        if identity.registry is not None:
            current = self._approval_for_registry(identity.source, identity.registry.value_name)
        else:
            detail = _require_folder(identity)
            current = self._read_optional_raw_value(
                winreg.HKEY_CURRENT_USER,
                _APPROVED_FOLDER_KEY,
                detail.shortcut_path.name,
                "NATIVE",
            )
        current_digest = hashlib.sha256(current[1]).hexdigest() if current else None
        if current_digest != expected:
            raise StartupIdentityChangedError("Windows StartupApproved state changed")

    def _read_optional_raw_value(
        self,
        hive: int,
        key_path: str,
        value_name: str,
        registry_view: str,
    ) -> tuple[int, bytes] | None:
        try:
            return self._read_raw_value(hive, key_path, value_name, registry_view)
        except FileNotFoundError:
            return None

    def _read_raw_value(
        self,
        hive: int,
        key_path: str,
        value_name: str,
        registry_view: str,
    ) -> tuple[int, bytes]:
        handle = ctypes.c_void_p()
        open_key = cast(Any, self._advapi32.RegOpenKeyExW)
        open_key.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        open_key.restype = ctypes.c_long
        result = int(
            open_key(
                ctypes.c_void_p(hive),
                key_path,
                0,
                _KEY_QUERY_VALUE | _view_flag(registry_view),
                ctypes.byref(handle),
            )
        )
        if result != _ERROR_SUCCESS:
            _raise_registry_error("Open startup registry key", result)
        try:
            return self._query_raw_handle(handle, value_name)
        finally:
            self._close_registry_key(handle)

    def _query_raw_handle(self, handle: ctypes.c_void_p, value_name: str) -> tuple[int, bytes]:
        query = cast(Any, self._advapi32.RegQueryValueExW)
        query.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        query.restype = ctypes.c_long
        value_type = ctypes.c_ulong()
        size = ctypes.c_ulong()
        result = int(
            query(
                handle,
                value_name,
                None,
                ctypes.byref(value_type),
                None,
                ctypes.byref(size),
            )
        )
        if result not in {_ERROR_SUCCESS, _ERROR_MORE_DATA}:
            _raise_registry_error("Read startup registry value size", result)
        buffer = ctypes.create_string_buffer(max(1, size.value))
        result = int(
            query(
                handle,
                value_name,
                None,
                ctypes.byref(value_type),
                buffer,
                ctypes.byref(size),
            )
        )
        if result != _ERROR_SUCCESS:
            _raise_registry_error("Read startup registry value", result)
        return int(value_type.value), bytes(buffer.raw[: size.value])

    def _delete_registry_value_transacted(self, detail: RegistryStartupIdentity) -> None:
        transaction, handle = self._open_transacted_run_key(
            detail, _KEY_QUERY_VALUE | _KEY_SET_VALUE
        )
        try:
            value_type, data = self._query_raw_handle(handle, detail.value_name)
            if value_type != detail.value_type or hashlib.sha256(data).hexdigest() != (
                detail.value_data_digest
            ):
                raise StartupIdentityChangedError()
            delete_value = cast(Any, self._advapi32.RegDeleteValueW)
            delete_value.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
            delete_value.restype = ctypes.c_long
            result = int(delete_value(handle, detail.value_name))
            if result != _ERROR_SUCCESS:
                _raise_registry_error("Disable HKCU Run value", result)
            self._commit_transaction(transaction)
        except Exception:
            self._rollback_transaction(transaction)
            raise
        finally:
            self._close_registry_key(handle)
            self._close_handle(transaction)

    def _set_registry_value_transacted(
        self,
        detail: RegistryStartupIdentity,
        value_type: int,
        data: bytes,
    ) -> None:
        transaction, handle = self._open_transacted_run_key(
            detail, _KEY_QUERY_VALUE | _KEY_SET_VALUE
        )
        try:
            try:
                self._query_raw_handle(handle, detail.value_name)
            except FileNotFoundError:
                pass
            else:
                raise StartupConflictError()
            set_value = cast(Any, self._advapi32.RegSetValueExW)
            set_value.argtypes = [
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_ulong,
                ctypes.c_ulong,
                ctypes.c_void_p,
                ctypes.c_ulong,
            ]
            set_value.restype = ctypes.c_long
            buffer = ctypes.create_string_buffer(data, max(1, len(data)))
            result = int(
                set_value(
                    handle,
                    detail.value_name,
                    0,
                    value_type,
                    buffer,
                    len(data),
                )
            )
            if result != _ERROR_SUCCESS:
                _raise_registry_error("Restore HKCU Run value", result)
            self._commit_transaction(transaction)
        except Exception:
            self._rollback_transaction(transaction)
            raise
        finally:
            self._close_registry_key(handle)
            self._close_handle(transaction)

    def _open_transacted_run_key(
        self,
        detail: RegistryStartupIdentity,
        access: int,
    ) -> tuple[ctypes.c_void_p, ctypes.c_void_p]:
        if detail.hive != "HKCU" or detail.key_path != _RUN_KEY or detail.registry_view != "NATIVE":
            raise PermissionError("The registry adapter is fixed to native HKCU Run")
        create_transaction = cast(Any, self._ktmw32.CreateTransaction)
        create_transaction.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_wchar_p,
        ]
        create_transaction.restype = ctypes.c_void_p
        transaction = ctypes.c_void_p(
            create_transaction(None, None, 0, 0, 0, 30_000, "PC Manager startup mutation")
        )
        if not transaction.value or transaction.value == ctypes.c_void_p(-1).value:
            raise WindowsStartupManagementError("Could not create registry transaction")
        handle = ctypes.c_void_p()
        open_key = cast(Any, self._advapi32.RegOpenKeyTransactedW)
        open_key.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        open_key.restype = ctypes.c_long
        result = int(
            open_key(
                ctypes.c_void_p(winreg.HKEY_CURRENT_USER),
                _RUN_KEY,
                0,
                access,
                ctypes.byref(handle),
                transaction,
                None,
            )
        )
        if result != _ERROR_SUCCESS:
            self._close_handle(transaction)
            _raise_registry_error("Open transacted HKCU Run key", result)
        return transaction, handle

    def _commit_transaction(self, transaction: ctypes.c_void_p) -> None:
        commit = cast(Any, self._ktmw32.CommitTransaction)
        commit.argtypes = [ctypes.c_void_p]
        commit.restype = ctypes.c_int
        if not commit(transaction):
            raise WindowsStartupManagementError("Registry transaction commit failed")

    def _rollback_transaction(self, transaction: ctypes.c_void_p) -> None:
        rollback = cast(Any, self._ktmw32.RollbackTransaction)
        rollback.argtypes = [ctypes.c_void_p]
        rollback.restype = ctypes.c_int
        rollback(transaction)

    def _close_registry_key(self, handle: ctypes.c_void_p) -> None:
        close_key = cast(Any, self._advapi32.RegCloseKey)
        close_key.argtypes = [ctypes.c_void_p]
        close_key.restype = ctypes.c_long
        close_key(handle)

    def _close_handle(self, handle: ctypes.c_void_p) -> None:
        close = cast(Any, self._kernel32.CloseHandle)
        close.argtypes = [ctypes.c_void_p]
        close.restype = ctypes.c_int
        close(handle)


def _decode_registry_command(value_type: int, data: bytes) -> str:
    if value_type not in {_REG_SZ, _REG_EXPAND_SZ}:
        raise ValueError("Only REG_SZ and REG_EXPAND_SZ startup commands are supported")
    if len(data) % 2:
        raise ValueError("Registry command has invalid UTF-16 byte length")
    command = data.decode("utf-16-le", errors="strict").rstrip("\x00")
    if not command or "\x00" in command:
        raise ValueError("Registry command is empty or contains embedded NUL")
    return os.path.expandvars(command) if value_type == _REG_EXPAND_SZ else command


def _resolve_command(command: str) -> tuple[Path, int]:
    argv = _command_line_to_argv(command)
    if not argv:
        raise ValueError("Startup command does not contain an executable")
    return _resolve_executable_path(argv[0]), max(0, len(argv) - 1)


def _command_line_to_argv(command: str) -> tuple[str, ...]:
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    parse = cast(Any, shell32.CommandLineToArgvW)
    parse.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(ctypes.c_wchar_p)
    count = ctypes.c_int()
    values = parse(command, ctypes.byref(count))
    if not values:
        raise ValueError("Windows could not parse the startup command")
    try:
        return tuple(values[index] for index in range(count.value))
    finally:
        local_free = cast(Any, kernel32.LocalFree)
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        local_free(values)


def _resolve_executable_path(raw: str) -> Path:
    expanded = os.path.expandvars(raw.strip().strip('"'))
    path = Path(expanded)
    if not path.is_absolute() or path.suffix.casefold() != ".exe":
        raise ValueError("Startup target must resolve to one absolute .exe file")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("Startup executable is not a regular file")
    return resolved


def _read_shell_link(path: Path) -> tuple[str, str, str]:
    link = cast(
        Any,
        pythoncom.CoCreateInstance(
            shell.CLSID_ShellLink,
            None,
            pythoncom.CLSCTX_INPROC_SERVER,
            shell.IID_IShellLink,
        ),
    )
    persist_file = cast(Any, link.QueryInterface(pythoncom.IID_IPersistFile))
    persist_file.Load(str(path))
    target, _find_data = link.GetPath(shell.SLGP_RAWPATH)
    return str(target), str(link.GetArguments()), str(link.GetWorkingDirectory())


def _publisher(path: Path) -> str | None:
    try:
        get_version = cast(Any, win32api.GetFileVersionInfo)
        translations = get_version(str(path), r"\VarFileInfo\Translation")
        if not translations:
            return None
        language, codepage = translations[0]
        value = get_version(
            str(path),
            rf"\StringFileInfo\{language:04x}{codepage:04x}\CompanyName",
        )
    except (OSError, TypeError, ValueError):
        return None
    text = str(value).strip()
    return text or None


def _approval_status(
    approval: tuple[int, bytes] | None,
) -> tuple[StartupEntryStatus, str]:
    if approval is None:
        return StartupEntryStatus.ENABLED, "No StartupApproved override was present"
    value_type, data = approval
    # StartupApproved is undocumented. These exact 12-byte states are treated only as
    # conservative evidence. The adapter never writes this binary contract.
    if value_type == winreg.REG_BINARY and len(data) == 12 and data[0] == 2:
        return StartupEntryStatus.ENABLED, "StartupApproved contains a recognized enabled state"
    if value_type == winreg.REG_BINARY and len(data) == 12 and data[0] == 3:
        return (
            StartupEntryStatus.DISABLED_BY_WINDOWS,
            "Windows already marks this entry disabled; the Agent will not take ownership",
        )
    return StartupEntryStatus.UNKNOWN, "StartupApproved data is not safely recognized"


def _unresolved_registry_observation(
    source: StartupSource,
    hive_name: str,
    key_path: str,
    registry_view: str,
    name: str,
) -> StartupObservation:
    placeholder = hashlib.sha256(
        f"{hive_name}\n{key_path}\n{name}\n{registry_view}".encode()
    ).hexdigest()
    return StartupObservation(
        identity=StartupIdentity(
            source=source,
            registry=RegistryStartupIdentity(
                hive=hive_name,
                key_path=key_path,
                value_name=name,
                value_type=0,
                value_data_digest=placeholder,
                command_fingerprint=placeholder,
                resolved_executable_path=None,
                registry_view=registry_view,
            ),
        ),
        display_name=name,
        command_summary="Unresolved registry command (content withheld)",
        scope="CURRENT_USER" if hive_name == "HKCU" else "ALL_USERS",
        status=StartupEntryStatus.UNKNOWN,
        status_evidence="Value could not be decoded or resolved safely",
        management_mode=StartupManagementMode.READ_ONLY,
    )


def _unresolved_folder_observation(
    source: StartupSource,
    path: Path,
) -> StartupObservation:
    digest = hashlib.sha256(str(path).casefold().encode()).hexdigest()
    return StartupObservation(
        identity=StartupIdentity(
            source=source,
            folder=FolderStartupIdentity(
                shortcut_path=path.resolve(strict=False),
                volume_serial=0,
                file_id="unresolved",
                shortcut_digest=digest,
                resolved_target=Path(r"C:\unresolved.exe"),
                arguments_fingerprint=digest,
                working_directory_fingerprint=digest,
            ),
        ),
        display_name=path.stem,
        command_summary="Unresolved Startup Folder item (content withheld)",
        scope=("CURRENT_USER" if source is StartupSource.USER_STARTUP_FOLDER else "ALL_USERS"),
        status=StartupEntryStatus.UNKNOWN,
        status_evidence="Shortcut type, identity, or target could not be resolved safely",
        management_mode=StartupManagementMode.READ_ONLY,
    )


def _require_registry(identity: StartupIdentity) -> RegistryStartupIdentity:
    if identity.registry is None:
        raise ValueError("Registry startup identity is required")
    return identity.registry


def _require_folder(identity: StartupIdentity) -> FolderStartupIdentity:
    if identity.folder is None:
        raise ValueError("Startup Folder identity is required")
    return identity.folder


def _require_disabled_path(payload: StartupBackupPayload) -> Path:
    if payload.disabled_storage_path is None:
        raise ValueError("Disabled storage path is missing")
    return payload.disabled_storage_path


def _read_bounded_file(path: Path, maximum: int = 16 * 1024 * 1024) -> bytes:
    size = path.stat(follow_symlinks=False).st_size
    if size > maximum:
        raise ValueError("Startup shortcut is too large to manage safely")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Startup shortcut exceeded the backup limit")
    return data


def _view_flag(registry_view: str) -> int:
    return {
        "NATIVE": 0,
        "32": _KEY_WOW64_32KEY,
        "64": _KEY_WOW64_64KEY,
    }[registry_view]


def _raise_registry_error(action: str, code: int) -> None:
    if code == _ERROR_FILE_NOT_FOUND:
        raise FileNotFoundError(code, f"{action}: registry object not found")
    if code == _ERROR_ACCESS_DENIED:
        raise PermissionError(code, f"{action}: ordinary-user access denied")
    raise WindowsStartupManagementError(code, f"{action} failed (Windows error {code})")
