"""Fail-closed inspection for frozen release directories and Broker dependency isolation."""

from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

_FORBIDDEN_NAMES = {
    ".env",
    ".env.local",
    "state.db",
    "application.jsonl",
    "cookies.sqlite",
}
_FORBIDDEN_SUFFIXES = {
    ".py",
    ".pyc",
    ".pyo",
    ".key",
    ".p12",
    ".pfx",
    ".db",
    ".sqlite",
}
_FORBIDDEN_AMBIENT_DLLS = {"icuuc.dll", "icudt78.dll"}
_INLINE_SECRET = re.compile(rb"(?i)(?<![a-z0-9_])(?:gh[pousr]_[a-z0-9_]{20,}|sk-[a-z0-9_-]{12,})")
_PRIVATE_KEY_BLOCK = re.compile(
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s+"
    rb"[A-Za-z0-9+/=\r\n]{64,}\s+"
    rb"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
_BROKER_FORBIDDEN = (
    "PySide6",
    "openai",
    "pc_manager_agent.browser",
    "pc_manager_agent.office",
    "pc_manager_agent.providers",
    "pc_manager_agent.ui",
    "pc_manager_agent.voice",
    "pc_manager_agent.privileged.mock_broker",
)


class ArtifactInspectionError(RuntimeError):
    """Stable failure for unexpected or unsafe packaged content."""


@dataclass(frozen=True, slots=True)
class ArtifactInventory:
    """Content-free inventory returned after successful inspection."""

    files: int
    bytes: int
    sha256: str


def inspect_directory(root: Path, expected_executable: str) -> ArtifactInventory:
    """Require one expected executable and reject source, state, credentials and test fixtures."""
    resolved = root.resolve(strict=True)
    if not resolved.is_dir() or not (resolved / expected_executable).is_file():
        raise ArtifactInspectionError("ARTIFACT_LAYOUT_INVALID")
    files = tuple(path for path in resolved.rglob("*") if path.is_file())
    if not files:
        raise ArtifactInspectionError("ARTIFACT_EMPTY")
    digest = hashlib.sha256()
    total = 0
    for path in sorted(files):
        relative = path.relative_to(resolved).as_posix()
        lowered = path.name.casefold()
        if lowered in _FORBIDDEN_NAMES or path.suffix.casefold() in _FORBIDDEN_SUFFIXES:
            raise ArtifactInspectionError(f"ARTIFACT_FORBIDDEN_FILE:{relative}")
        if lowered in _FORBIDDEN_AMBIENT_DLLS:
            raise ArtifactInspectionError(f"ARTIFACT_AMBIENT_DLL:{relative}")
        if "test" in {part.casefold() for part in path.relative_to(resolved).parts[:-1]}:
            raise ArtifactInspectionError(f"ARTIFACT_TEST_DATA:{relative}")
        file_digest, marker_found = _hash_and_scan(path)
        if marker_found:
            raise ArtifactInspectionError(f"ARTIFACT_SECRET_MARKER:{relative}")
        if path.suffix.casefold() == ".pem" and _contains_private_key(path):
            raise ArtifactInspectionError(f"ARTIFACT_PRIVATE_KEY:{relative}")
        total += path.stat().st_size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest)
    return ArtifactInventory(files=len(files), bytes=total, sha256=digest.hexdigest())


def _hash_and_scan(path: Path) -> tuple[bytes, bool]:
    """Hash one file in bounded chunks and find markers that cross chunk boundaries."""
    digest = hashlib.sha256()
    overlap = 255
    previous = b""
    marker_found = False
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            scanned = previous + chunk
            marker_found = marker_found or _INLINE_SECRET.search(scanned) is not None
            previous = scanned[-overlap:]
    return digest.digest(), marker_found


def _contains_private_key(path: Path) -> bool:
    """Allow public CA PEM bundles but reject bounded encoded private-key blocks."""
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ArtifactInspectionError(f"ARTIFACT_PEM_TOO_LARGE:{path.name}")
    return _PRIVATE_KEY_BLOCK.search(path.read_bytes()) is not None


def inspect_broker_xref(path: Path) -> None:
    """Reject GUI/model/browser/voice/Office/Mock modules in the Broker analysis graph."""
    content = path.resolve(strict=True).read_text(encoding="utf-8", errors="replace")
    found = tuple(name for name in _BROKER_FORBIDDEN if name in content)
    if found:
        raise ArtifactInspectionError(f"BROKER_DEPENDENCY_FORBIDDEN:{','.join(found)}")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse explicit build locations; current directory never selects an artifact implicitly."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--main", type=Path, required=True)
    parser.add_argument("--broker", type=Path, required=True)
    parser.add_argument("--browser-worker", type=Path, required=True)
    parser.add_argument("--broker-xref", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Inspect all three isolated outputs and print only aggregate evidence."""
    arguments = parse_arguments(argv)
    products = (
        (arguments.main, "pc-manager-agent.exe"),
        (arguments.broker, "pc-manager-privileged-broker.exe"),
        (arguments.browser_worker, "pc-manager-browser-worker.exe"),
    )
    for root, executable in products:
        evidence = inspect_directory(root, executable)
        print(
            f"{executable}: files={evidence.files} bytes={evidence.bytes} sha256={evidence.sha256}"
        )
    inspect_broker_xref(arguments.broker_xref)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
