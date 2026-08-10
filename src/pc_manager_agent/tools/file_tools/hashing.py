"""Cancellable, identity-checked hashing for duplicate-file candidates."""

from __future__ import annotations

import hashlib
import os
from typing import BinaryIO, Protocol

from pc_manager_agent.domain.errors import FileChangedDuringScanError, ScanCancelledError
from pc_manager_agent.domain.file_analysis import StoredFileRecord
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.tools.manifest import CancellationToken


class _Digest(Protocol):
    """Minimal hash interface used by the cancellable stream loop."""

    def update(self, data: bytes) -> None:
        """Add bytes to the digest state."""
        ...


class SafeFileHasher:
    """Read only approved files and reject identity or metadata changes."""

    def __init__(self, path_policy: PathPolicy, *, chunk_size: int = 1_048_576) -> None:
        self._path_policy = path_policy
        self._chunk_size = max(4_096, chunk_size)

    def quick_hash(
        self,
        record: StoredFileRecord,
        cancellation: CancellationToken,
        *,
        sample_bytes: int = 65_536,
    ) -> str:
        """Hash file size plus bounded first and last byte samples."""
        sample_size = max(4_096, sample_bytes)
        digest = hashlib.sha256()
        digest.update(record.metadata.size_bytes.to_bytes(16, "big", signed=False))
        with self._open_verified(record) as handle:
            if record.metadata.size_bytes <= sample_size * 2:
                self._hash_stream(handle, digest, cancellation)
            else:
                self._raise_if_cancelled(cancellation)
                digest.update(handle.read(sample_size))
                handle.seek(-sample_size, os.SEEK_END)
                self._raise_if_cancelled(cancellation)
                digest.update(handle.read(sample_size))
            self._verify_open_handle(handle, record)
        return digest.hexdigest()

    def sha256(self, record: StoredFileRecord, cancellation: CancellationToken) -> str:
        """Return a full SHA-256 after checking the open handle before and after."""
        digest = hashlib.sha256()
        with self._open_verified(record) as handle:
            self._hash_stream(handle, digest, cancellation)
            self._verify_open_handle(handle, record)
        return digest.hexdigest()

    def byte_equal(
        self,
        left: StoredFileRecord,
        right: StoredFileRecord,
        cancellation: CancellationToken,
    ) -> bool:
        """Compare two verified candidates chunk by chunk without modifying them."""
        if left.metadata.size_bytes != right.metadata.size_bytes:
            return False
        with self._open_verified(left) as left_handle, self._open_verified(right) as right_handle:
            while True:
                self._raise_if_cancelled(cancellation)
                left_chunk = left_handle.read(self._chunk_size)
                right_chunk = right_handle.read(self._chunk_size)
                if left_chunk != right_chunk:
                    return False
                if not left_chunk:
                    break
            self._verify_open_handle(left_handle, left)
            self._verify_open_handle(right_handle, right)
        return True

    def _open_verified(self, record: StoredFileRecord) -> BinaryIO:
        if record.metadata.offline:
            raise FileChangedDuringScanError(
                f"Offline placeholder was not hydrated for hashing: {record.metadata.path}"
            )
        path = self._path_policy.validate_file(record.metadata.path)
        handle = path.open("rb")
        try:
            self._verify_open_handle(handle, record)
        except Exception:
            handle.close()
            raise
        return handle

    def _hash_stream(
        self,
        handle: BinaryIO,
        digest: _Digest,
        cancellation: CancellationToken,
    ) -> None:
        while True:
            self._raise_if_cancelled(cancellation)
            chunk = handle.read(self._chunk_size)
            if not chunk:
                return
            digest.update(chunk)

    @staticmethod
    def _verify_open_handle(handle: BinaryIO, record: StoredFileRecord) -> None:
        metadata = os.fstat(handle.fileno())
        expected = record.metadata
        changed = metadata.st_size != expected.size_bytes
        if expected.file_id is not None:
            changed = changed or metadata.st_ino != expected.file_id
        if expected.device_id is not None:
            changed = changed or metadata.st_dev != expected.device_id
        expected_modified = expected.modified_at.timestamp()
        changed = changed or abs(metadata.st_mtime - expected_modified) > 0.001
        if changed:
            raise FileChangedDuringScanError(f"File changed after metadata scan: {expected.path}")

    @staticmethod
    def _raise_if_cancelled(cancellation: CancellationToken) -> None:
        if cancellation.cancellation_requested():
            raise ScanCancelledError("Hashing was cancelled")
