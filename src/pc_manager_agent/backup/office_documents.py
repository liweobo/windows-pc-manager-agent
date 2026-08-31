"""Verified binary Office backups. Call only through confirmed R1 backup preparation."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from pc_manager_agent.config.office import OfficeLimits
from pc_manager_agent.domain.office_documents import OfficeDocumentIdentity, OfficeError
from pc_manager_agent.domain.office_transactions import OfficeDocumentBackup
from pc_manager_agent.persistence.office_documents import OfficeRepository
from pc_manager_agent.platform_support.windows.office_files import WindowsOfficeFiles
from pc_manager_agent.platform_support.windows.office_protection import WindowsOfficeDataProtector
from pc_manager_agent.safety.path_policy import is_reparse_point
from pc_manager_agent.tools.manifest import CancellationToken


class DocumentBackupService:
    """Keep encrypted, immutable, verified copies; never automatically delete backups."""

    def __init__(
        self,
        root: Path,
        repository: OfficeRepository,
        files: WindowsOfficeFiles,
        protector: WindowsOfficeDataProtector,
        limits: OfficeLimits,
    ) -> None:
        self._root = root
        self._repository = repository
        self._files = files
        self._protector = protector
        self._limits = limits
        self._lock = RLock()

    def create(
        self,
        data: bytes,
        identity: OfficeDocumentIdentity,
        cancellation: CancellationToken,
        transaction_id: UUID,
    ) -> OfficeDocumentBackup:
        """Encrypt, exclusive-create, read back and hash before publishing a backup record."""
        if hashlib.sha256(data).hexdigest() != identity.sha256:
            raise OfficeError("BACKUP_SOURCE_CHANGED")
        with self._lock, self._files.pin_parents(self._root):
            if not self._root.exists():
                self._root.mkdir()
            if is_reparse_point(self._root) or not self._root.is_dir():
                raise OfficeError("BACKUP_LOCATION_UNSAFE")
            entries = tuple(self._root.iterdir())
            if len(entries) > 10_000 or any(
                is_reparse_point(item) or not item.is_file() for item in entries
            ):
                raise OfficeError("BACKUP_STORE_UNSAFE_OR_FULL")
            encrypted = self._protector.protect(data)
            if (
                sum(item.stat().st_size for item in entries) + len(encrypted)
                > self._limits.backup_bytes
            ):
                raise OfficeError("BACKUP_QUOTA_EXCEEDED")
            backup_id = uuid4()
            target = self._root / f"{backup_id}.bin"
            with self._files.pin_parents(target), self._files.open(target, create=True) as lease:
                lease.write_new(encrypted)
                stored = lease.read(len(encrypted), cancellation)
                if stored != encrypted or self._protector.unprotect(stored) != data:
                    raise OfficeError("BACKUP_VERIFICATION_FAILED")
            record = OfficeDocumentBackup(
                backup_id=backup_id,
                transaction_id=transaction_id,
                source=identity,
                path=target,
                cipher_sha256=hashlib.sha256(encrypted).hexdigest(),
                size_bytes=len(encrypted),
                created_at=datetime.now(UTC),
            )
            self._repository.put_backup(record)
            return record

    def restore_bytes(self, backup_id: UUID, cancellation: CancellationToken) -> bytes:
        """Validate exact managed location, encrypted hash and original plaintext hash."""
        record = self._repository.backup(backup_id)
        if record.path != self._root / f"{backup_id}.bin":
            raise OfficeError("BACKUP_LOCATION_CHANGED")
        with self._files.pin_parents(record.path), self._files.open(record.path) as lease:
            stored = lease.read(self._limits.other_bytes + 1024**2, cancellation)
        if hashlib.sha256(stored).hexdigest() != record.cipher_sha256:
            raise OfficeError("BACKUP_CORRUPT")
        data = self._protector.unprotect(stored)
        if hashlib.sha256(data).hexdigest() != record.source.sha256:
            raise OfficeError("BACKUP_VERIFICATION_FAILED")
        return data
