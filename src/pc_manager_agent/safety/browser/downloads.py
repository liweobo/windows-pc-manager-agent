"""Filename, media-type, size, magic, and conditional recovery checks."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
from pathlib import Path
from uuid import uuid4

from pc_manager_agent.domain.browser_downloads import (
    BrowserDownloadIdentity,
    BrowserDownloadPreview,
    BrowserDownloadRecoveryRecord,
)


class BrowserDownloadPolicyError(ValueError):
    """Raised when an untrusted download violates the finite document policy."""


_ALLOWED: dict[str, frozenset[str]] = {
    ".pdf": frozenset({"application/pdf"}),
    ".txt": frozenset({"text/plain"}),
    ".csv": frozenset({"text/csv", "text/plain", "application/csv"}),
    ".json": frozenset({"application/json", "text/json", "text/plain"}),
    ".docx": frozenset({"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}),
    ".xlsx": frozenset({"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}),
    ".png": frozenset({"image/png"}),
    ".jpg": frozenset({"image/jpeg"}),
    ".jpeg": frozenset({"image/jpeg"}),
    ".gif": frozenset({"image/gif"}),
    ".webp": frozenset({"image/webp"}),
}
_RETAIN_REASONS = frozenset({"failed-commit", "filename-mismatch", "validated-staging-copy"})
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_BIDI_CONTROLS = re.compile("[\u202a-\u202e\u2066-\u2069]")


class BrowserDownloadPolicy:
    """Allow one ordinary document/image with consistent extension, MIME, and magic."""

    def validate_filename(self, filename: str) -> str:
        """Reject traversal, ADS, device names, controls, and misleading Unicode controls."""
        if not filename or len(filename) > 255:
            raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_FILENAME_INVALID")
        path = Path(filename)
        if path.is_absolute() or path.name != filename or filename in {".", ".."}:
            raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_PATH_TRAVERSAL_BLOCKED")
        if any(ord(character) < 32 for character in filename) or _BIDI_CONTROLS.search(filename):
            raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_CONTROL_CHARACTER_BLOCKED")
        if any(character in filename for character in '<>:"/\\|?*'):
            raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_WINDOWS_NAME_BLOCKED")
        stem = path.stem.rstrip(" .").upper()
        if stem in _RESERVED or filename.endswith((" ", ".")):
            raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_RESERVED_NAME_BLOCKED")
        if path.suffix.casefold() not in _ALLOWED:
            raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_TYPE_BLOCKED")
        return filename

    def validate_declared(
        self,
        filename: str,
        mime_type: str,
        size_bytes: int | None,
        *,
        max_size_bytes: int,
    ) -> None:
        """Reject a preflight that is missing or contradicts the finite allow-list."""
        self.validate_filename(filename)
        if max_size_bytes <= 0 or (size_bytes is not None and size_bytes > max_size_bytes):
            raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_SIZE_BLOCKED")
        suffix = Path(filename).suffix.casefold()
        normalized_mime = mime_type.partition(";")[0].strip().casefold()
        if normalized_mime not in _ALLOWED[suffix]:
            raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_MIME_MISMATCH")

    def validate_completed(self, preview: BrowserDownloadPreview, temporary_path: Path) -> str:
        """Revalidate regular-file identity, size, extension/MIME, and fixed magic bytes."""
        try:
            metadata = temporary_path.lstat()
        except OSError as exc:
            raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_TEMP_UNAVAILABLE") from exc
        if not stat.S_ISREG(metadata.st_mode) or temporary_path.is_symlink():
            raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_TEMP_NOT_REGULAR")
        if metadata.st_size > preview.max_size_bytes:
            raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_SIZE_BLOCKED")
        with temporary_path.open("rb") as stream:
            head = stream.read(16)
        suffix = Path(preview.suggested_filename).suffix.casefold()
        if not _magic_matches(suffix, head):
            raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_MAGIC_MISMATCH")
        return _detected_mime(suffix)


class BrowserDownloadManager:
    """Commit one validated temp download with no overwrite and truthful recovery evidence."""

    def __init__(self, policy: BrowserDownloadPolicy | None = None) -> None:
        self._policy = policy or BrowserDownloadPolicy()

    def commit(
        self,
        preview: BrowserDownloadPreview,
        temporary_path: Path,
    ) -> tuple[BrowserDownloadIdentity, BrowserDownloadRecoveryRecord]:
        """Copy to an O_EXCL destination, hash it, and retain a conditional recovery target."""
        detected_mime = self._policy.validate_completed(preview, temporary_path)
        destination = preview.destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as target, temporary_path.open("rb") as source:
                shutil.copyfileobj(source, target, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            size, sha256 = _hash_file(destination, preview.max_size_bytes)
        except Exception:
            if destination.exists():
                _retain_artifact(destination, "failed-commit")
            raise
        identity = BrowserDownloadIdentity(
            preview_id=preview.preview_id,
            path=destination,
            size_bytes=size,
            sha256=sha256,
            detected_mime_type=detected_mime,
        )
        recovery_path = (
            destination.parent
            / ".pc-manager-recovery"
            / (f"{identity.download_id}-{destination.name}")
        )
        return identity, BrowserDownloadRecoveryRecord(
            download=identity,
            recovery_path=recovery_path,
        )

    def retain_staged(self, temporary_path: Path, *, reason: str) -> Path:
        """Move a staged artifact to private recovery storage instead of deleting it."""
        return _retain_artifact(temporary_path, reason)

    def rollback(self, record: BrowserDownloadRecoveryRecord) -> Path:
        """Move an unchanged Agent-created download into retained recovery storage."""
        source = record.download.path
        size, sha256 = _hash_file(source, record.download.size_bytes)
        if size != record.download.size_bytes or sha256 != record.download.sha256:
            raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_ROLLBACK_CONFLICT")
        destination = record.recovery_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_RECOVERY_CONFLICT")
        source.replace(destination)
        return destination


def _hash_file(path: Path, maximum: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > maximum:
                raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_SIZE_BLOCKED")
            digest.update(chunk)
    return total, digest.hexdigest()


def _retain_artifact(path: Path, reason: str) -> Path:
    """Retain one exact artifact under a UUID name with no overwrite or delete fallback."""
    if reason not in _RETAIN_REASONS:
        raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_RETAIN_REASON_BLOCKED")
    if not path.is_file() or path.is_symlink():
        raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_RETAIN_SOURCE_INVALID")
    recovery_directory = path.parent / ".pc-manager-recovery"
    recovery_directory.mkdir(parents=True, exist_ok=True)
    destination = recovery_directory / f"{uuid4()}-{reason}.download"
    if destination.exists():
        raise BrowserDownloadPolicyError("BROWSER_DOWNLOAD_RETAIN_CONFLICT")
    path.rename(destination)
    return destination


def _magic_matches(suffix: str, head: bytes) -> bool:
    if suffix == ".pdf":
        return head.startswith(b"%PDF-")
    if suffix in {".docx", ".xlsx"}:
        return head.startswith(b"PK\x03\x04")
    if suffix == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in {".jpg", ".jpeg"}:
        return head.startswith(b"\xff\xd8\xff")
    if suffix == ".gif":
        return head.startswith((b"GIF87a", b"GIF89a"))
    if suffix == ".webp":
        return head.startswith(b"RIFF") and head[8:12] == b"WEBP"
    if suffix in {".txt", ".csv", ".json"}:
        return b"\x00" not in head
    return False


def _detected_mime(suffix: str) -> str:
    return {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".csv": "text/csv",
        ".json": "application/json",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }[suffix]
