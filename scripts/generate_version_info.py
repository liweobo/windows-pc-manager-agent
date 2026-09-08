"""Generate PyInstaller version resources from the package's single version source."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_VERSION_PATTERN = re.compile(
    r'^__version__\s*=\s*"(?P<value>\d+\.\d+\.\d+(?:-rc\.\d+)?)"$',
    re.MULTILINE,
)
_SEMVER_PATTERN = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-rc\.(?P<rc>\d+))?$"
)


def read_version(source: Path) -> str:
    """Read a final or release-candidate SemVer without importing project code."""
    match = _VERSION_PATTERN.search(source.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError("VERSION_SOURCE_INVALID")
    return match.group("value")


def render_version_info(version: str, original_filename: str, description: str) -> str:
    """Render a deterministic Windows VERSIONINFO resource for one executable."""
    match = _SEMVER_PATTERN.fullmatch(version)
    if match is None:
        raise ValueError("VERSION_INVALID")
    parts = tuple(int(match.group(name) or 0) for name in ("major", "minor", "patch", "rc"))
    if any(part > 65535 for part in parts):
        raise ValueError("VERSION_COMPONENT_OUT_OF_RANGE")
    numeric = ", ".join(str(part) for part in parts)
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({numeric}), prodvers=({numeric}), mask=0x3f,
    flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'liweobo'),
    StringStruct('FileDescription', '{description}'),
    StringStruct('FileVersion', '{version}'),
    StringStruct('InternalName', '{Path(original_filename).stem}'),
    StringStruct('LegalCopyright', 'Copyright (c) liweobo'),
    StringStruct('OriginalFilename', '{original_filename}'),
    StringStruct('ProductName', 'Windows PC Manager Agent'),
    StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"""


def generate_all(version_source: Path, output_directory: Path) -> tuple[Path, ...]:
    """Create all release version files under an Agent-owned build directory."""
    version = read_version(version_source)
    output_directory.mkdir(parents=True, exist_ok=True)
    products = (
        ("pc-manager-agent", "pc-manager-agent.exe", "Windows PC Manager Agent"),
        (
            "pc-manager-privileged-broker",
            "pc-manager-privileged-broker.exe",
            "Windows PC Manager Agent Privileged Broker",
        ),
        (
            "pc-manager-browser-worker",
            "pc-manager-browser-worker.exe",
            "Windows PC Manager Agent Browser Worker",
        ),
    )
    generated = []
    for stem, filename, description in products:
        target = output_directory / f"{stem}-version.txt"
        target.write_text(
            render_version_info(version, filename, description),
            encoding="utf-8",
            newline="\n",
        )
        generated.append(target)
    return tuple(generated)


def main(argv: list[str] | None = None) -> int:
    """Generate resources from explicit paths and return a script-friendly status."""
    parser = argparse.ArgumentParser()
    parser.add_argument("version_source", type=Path)
    parser.add_argument("output_directory", type=Path)
    arguments = parser.parse_args(argv)
    generate_all(arguments.version_source, arguments.output_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
