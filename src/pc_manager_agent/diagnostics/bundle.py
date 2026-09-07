"""Reviewed, redacted, local-only diagnostic ZIP export with single-use authority."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from pc_manager_agent import __version__
from pc_manager_agent.config.production import BuildMode, ReleaseFeature
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.observability.logging import LogRedactionPolicy
from pc_manager_agent.persistence.migrations import MigrationReport
from pc_manager_agent.platform_support.windows.path_info import is_network_path
from pc_manager_agent.safety.path_policy import is_reparse_point

_SAFE_CODE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
_ENTRY_NAMES = ("manifest.json", "recent-logs.jsonl")
_EXCLUSIONS = (
    "API keys, passwords, cookies, MFA and provider credentials",
    "audit database and full audit content",
    "Memory values, task bodies and confirmation/Broker secrets",
    "document, webpage, transcript and raw audio content",
    "browser profiles, sessions and downloaded files",
    "user files, absolute paths and database backups",
)


class DiagnosticBundleError(RuntimeError):
    """Stable failure for an unsafe, stale, changed, or consumed support export."""


class DiagnosticRuntimeMetadata(BaseModel):
    """Finite non-content runtime facts permitted in the support manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    app_version: str
    build_mode: BuildMode
    safe_mode: bool
    enabled_features: tuple[ReleaseFeature, ...]
    provider: str
    schema_version: int = Field(ge=0)
    config_version: int = Field(ge=0)
    schema_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    os_name: str
    os_release: str
    architecture: str
    python_version: str
    frozen_binary: bool
    signing_status: str
    telemetry: str = "NOT_IMPLEMENTED"
    automatic_upload: bool = False


class DiagnosticEntrySummary(BaseModel):
    """Exact review metadata for one proposed ZIP member."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DiagnosticBundlePreview(BaseModel):
    """User-reviewable, expiring binding created before any output file exists."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    preview_id: UUID
    output_path: Path
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: tuple[DiagnosticEntrySummary, ...]
    exclusions: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    risk_level: str = "R1"
    rollback_level: str = "MANUAL"
    automatically_uploaded: bool = False


class DiagnosticBundleAuthority(BaseModel):
    """Volatile single-use approval secret bound to one exact Preview digest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    preview_id: UUID
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    secret: SecretStr = Field(repr=False)
    expires_at: datetime


class DiagnosticBundleResult(BaseModel):
    """Verified local output evidence; export never implies external transmission."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    output_path: Path
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: tuple[str, ...]
    automatically_uploaded: bool = False


@dataclass(slots=True)
class _PendingBundle:
    preview: DiagnosticBundlePreview
    entries: dict[str, bytes]
    authority_hash: str | None = None
    consumed: bool = False


class DiagnosticBundleService:
    """Prepare, approve, and exclusively export a redacted local support bundle."""

    def __init__(
        self,
        data_directory: Path,
        *,
        ttl_seconds: int = 300,
        max_log_bytes: int = 256 * 1024,
        redaction: LogRedactionPolicy | None = None,
        on_confirmation: Callable[[DiagnosticBundlePreview, bool], None] | None = None,
        on_exported: Callable[[DiagnosticBundleResult], None] | None = None,
    ) -> None:
        if ttl_seconds < 30 or ttl_seconds > 1_800:
            raise ValueError("Diagnostic Preview TTL is outside the supported policy")
        if max_log_bytes < 4_096 or max_log_bytes > 2 * 1024 * 1024:
            raise ValueError("Diagnostic log bound is outside the supported policy")
        self._data_directory = data_directory
        self._ttl_seconds = ttl_seconds
        self._max_log_bytes = max_log_bytes
        self._redaction = redaction or LogRedactionPolicy()
        self._on_confirmation = on_confirmation
        self._on_exported = on_exported
        self._pending: dict[UUID, _PendingBundle] = {}
        self._lock = RLock()

    def prepare(
        self,
        output_path: Path,
        metadata: DiagnosticRuntimeMetadata,
        recent_error_codes: tuple[str, ...] = (),
        *,
        now: datetime | None = None,
    ) -> DiagnosticBundlePreview:
        """Build volatile sanitized bytes and return their exact review digest without writing."""
        timestamp = now or datetime.now(UTC)
        target = self._validate_target(output_path)
        codes = _validate_error_codes(recent_error_codes)
        manifest = {
            "bundle_schema_version": 1,
            "generated_at": timestamp.isoformat(),
            "runtime": metadata.model_dump(mode="json"),
            "recent_error_codes": codes,
            "included_entries": _ENTRY_NAMES,
            "explicit_exclusions": _EXCLUSIONS,
            "automatic_upload": False,
        }
        entries = {
            "manifest.json": _json_bytes(manifest),
            "recent-logs.jsonl": self._sanitized_logs(),
        }
        digest = _entries_digest(entries)
        preview = DiagnosticBundlePreview(
            preview_id=uuid4(),
            output_path=target,
            content_digest=digest,
            entries=tuple(
                DiagnosticEntrySummary(
                    name=name,
                    size_bytes=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
                for name, content in sorted(entries.items())
            ),
            exclusions=_EXCLUSIONS,
            created_at=timestamp,
            expires_at=timestamp + timedelta(seconds=self._ttl_seconds),
        )
        with self._lock:
            self._discard_expired(timestamp)
            self._pending[preview.preview_id] = _PendingBundle(preview, entries)
        return preview

    def approve(
        self,
        preview: DiagnosticBundlePreview,
        *,
        now: datetime | None = None,
    ) -> DiagnosticBundleAuthority:
        """Bind explicit review to the exact live Preview; approval does not write the ZIP."""
        timestamp = now or datetime.now(UTC)
        with self._lock:
            pending = self._pending.get(preview.preview_id)
            if pending is None or pending.preview != preview:
                raise DiagnosticBundleError("DIAGNOSTIC_PREVIEW_CHANGED")
            if timestamp > preview.expires_at:
                del self._pending[preview.preview_id]
                raise DiagnosticBundleError("DIAGNOSTIC_PREVIEW_EXPIRED")
            if pending.authority_hash is not None or pending.consumed:
                raise DiagnosticBundleError("DIAGNOSTIC_AUTHORITY_ALREADY_ISSUED")
            secret = uuid4().hex + uuid4().hex
            if self._on_confirmation is not None:
                self._on_confirmation(preview, True)
            pending.authority_hash = _secret_hash(secret)
        return DiagnosticBundleAuthority(
            preview_id=preview.preview_id,
            content_digest=preview.content_digest,
            secret=secret,
            expires_at=preview.expires_at,
        )

    def reject(self, preview: DiagnosticBundlePreview) -> None:
        """Record explicit rejection and discard all volatile bundle bytes."""
        with self._lock:
            pending = self._pending.get(preview.preview_id)
            if pending is None or pending.preview != preview:
                raise DiagnosticBundleError("DIAGNOSTIC_PREVIEW_CHANGED")
            if self._on_confirmation is not None:
                self._on_confirmation(preview, False)
            del self._pending[preview.preview_id]

    def export(
        self,
        authority: DiagnosticBundleAuthority,
        *,
        now: datetime | None = None,
    ) -> DiagnosticBundleResult:
        """Atomically consume approval, create an absent ZIP, and verify exact members."""
        timestamp = now or datetime.now(UTC)
        with self._lock:
            pending = self._pending.get(authority.preview_id)
            if pending is None or pending.consumed or pending.authority_hash is None:
                raise DiagnosticBundleError("DIAGNOSTIC_AUTHORITY_INVALID")
            if timestamp > authority.expires_at or timestamp > pending.preview.expires_at:
                del self._pending[authority.preview_id]
                raise DiagnosticBundleError("DIAGNOSTIC_AUTHORITY_EXPIRED")
            if authority.content_digest != pending.preview.content_digest or not _constant_equal(
                pending.authority_hash, _secret_hash(authority.secret.get_secret_value())
            ):
                raise DiagnosticBundleError("DIAGNOSTIC_AUTHORITY_INVALID")
            if _entries_digest(pending.entries) != pending.preview.content_digest:
                raise DiagnosticBundleError("DIAGNOSTIC_CONTENT_CHANGED")
            pending.consumed = True
            entries = dict(pending.entries)
            target = self._validate_target(pending.preview.output_path)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        committed = False
        try:
            with ZipFile(temporary, "x", compression=ZIP_DEFLATED) as archive:
                for name, content in sorted(entries.items()):
                    archive.writestr(name, content)
            with temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
            _verify_archive(temporary, entries)
            os.link(temporary, target)
            committed = True
            temporary.unlink()
            _verify_archive(target, entries)
            result = DiagnosticBundleResult(
                output_path=target,
                size_bytes=target.stat().st_size,
                sha256=_sha256_file(target),
                entries=tuple(sorted(entries)),
            )
            if self._on_exported is not None:
                self._on_exported(result)
            return result
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            if committed:
                target.unlink(missing_ok=True)
            raise DiagnosticBundleError("DIAGNOSTIC_EXPORT_FAILED") from exc
        finally:
            with self._lock:
                self._pending.pop(authority.preview_id, None)

    def _sanitized_logs(self) -> bytes:
        log_directory = self._data_directory / "logs"
        if not log_directory.is_dir() or is_reparse_point(log_directory):
            return b""
        output: list[str] = []
        used = 0
        for path in sorted(log_directory.glob("application.jsonl*")):
            if is_reparse_point(path) or not path.is_file():
                continue
            with path.open("rb") as stream:
                for raw_line in stream:
                    if used + len(raw_line) > self._max_log_bytes:
                        return _lines_bytes(output)
                    used += len(raw_line)
                    output.append(self._sanitize_log_line(raw_line))
        return _lines_bytes(output)

    def _sanitize_log_line(self, raw_line: bytes) -> str:
        try:
            decoded = raw_line.decode("utf-8")
            value = json.loads(decoded)
            if not isinstance(value, dict):
                raise TypeError
            selected = {
                key: value[key]
                for key in (
                    "timestamp",
                    "severity",
                    "component",
                    "event_code",
                    "trace_id",
                    "task_id",
                )
                if key in value
            }
            exception = value.get("exception")
            if isinstance(exception, dict) and "type" in exception:
                selected["exception_type"] = exception["type"]
            redacted = self._redaction.redact(selected)
            return json.dumps(redacted, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            return '{"event_code":"MALFORMED_LOG_LINE","severity":"WARNING"}'

    def _validate_target(self, target: Path) -> Path:
        if (
            not target.is_absolute()
            or ".." in target.parts
            or any(part.rstrip(" .") != part for part in target.parts)
        ):
            raise DiagnosticBundleError("DIAGNOSTIC_TARGET_INVALID")
        if target.suffix.casefold() != ".zip" or target.exists():
            raise DiagnosticBundleError("DIAGNOSTIC_TARGET_INVALID")
        if not target.parent.is_dir() or _contains_reparse_component(target.parent):
            raise DiagnosticBundleError("DIAGNOSTIC_TARGET_INVALID")
        if is_network_path(target):
            raise DiagnosticBundleError("DIAGNOSTIC_TARGET_INVALID")
        return target.resolve(strict=False)

    def _discard_expired(self, now: datetime) -> None:
        expired = [
            preview_id
            for preview_id, pending in self._pending.items()
            if now > pending.preview.expires_at
        ]
        for preview_id in expired:
            del self._pending[preview_id]


def build_runtime_metadata(
    settings: AppSettings,
    migration: MigrationReport,
    *,
    frozen_binary: bool,
    signing_status: str = "NOT_CONFIGURED",
) -> DiagnosticRuntimeMetadata:
    """Collect a finite manifest without hostname, username, local path, or credential values."""
    return DiagnosticRuntimeMetadata(
        app_version=__version__,
        build_mode=settings.build_mode,
        safe_mode=settings.safe_mode,
        enabled_features=tuple(sorted(settings.feature_flags.enabled, key=lambda item: item.value)),
        provider=settings.llm_provider,
        schema_version=migration.to_version,
        config_version=migration.config_version,
        schema_digest=migration.schema_digest,
        os_name=platform.system(),
        os_release=platform.release(),
        architecture=platform.machine(),
        python_version=platform.python_version(),
        frozen_binary=frozen_binary,
        signing_status=signing_status,
    )


def _validate_error_codes(codes: tuple[str, ...]) -> tuple[str, ...]:
    if len(codes) > 50:
        raise DiagnosticBundleError("DIAGNOSTIC_ERROR_CODE_LIMIT")
    for code in codes:
        if not code or len(code) > 80 or any(char not in _SAFE_CODE for char in code):
            raise DiagnosticBundleError("DIAGNOSTIC_ERROR_CODE_INVALID")
    return codes


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _lines_bytes(lines: list[str]) -> bytes:
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def _entries_digest(entries: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(entries.items()):
        digest.update(name.encode("ascii"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def _secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


def _constant_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def _verify_archive(path: Path, expected: dict[str, bytes]) -> None:
    try:
        with ZipFile(path, "r") as archive:
            if archive.testzip() is not None or tuple(sorted(archive.namelist())) != tuple(
                sorted(expected)
            ):
                raise DiagnosticBundleError("DIAGNOSTIC_ARCHIVE_INVALID")
            for name, content in expected.items():
                if archive.read(name) != content:
                    raise DiagnosticBundleError("DIAGNOSTIC_ARCHIVE_INVALID")
    except BadZipFile as exc:
        raise DiagnosticBundleError("DIAGNOSTIC_ARCHIVE_INVALID") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contains_reparse_component(path: Path) -> bool:
    current = path
    while True:
        if is_reparse_point(current):
            return True
        if current.parent == current:
            return False
        current = current.parent
