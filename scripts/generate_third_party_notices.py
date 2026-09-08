"""Generate runtime-only dependency notices from the locked, installed environment."""

from __future__ import annotations

import argparse
import re
from collections import deque
from importlib import metadata
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

_LICENSE_CLASSIFIER_PREFIX = "License :: OSI Approved :: "


class LicenseAuditError(RuntimeError):
    """Stable failure when runtime dependency or license metadata is incomplete."""


def runtime_distributions(project_name: str) -> tuple[metadata.Distribution, ...]:
    """Resolve runtime dependencies, excluding optional extras and development groups."""
    available = {
        canonicalize_name(distribution.metadata["Name"]): distribution
        for distribution in metadata.distributions()
        if distribution.metadata.get("Name")
    }
    root = canonicalize_name(project_name)
    queue = deque([root])
    visited: set[str] = set()
    result: list[metadata.Distribution] = []
    while queue:
        name = queue.popleft()
        if name in visited:
            continue
        visited.add(name)
        distribution = available.get(name)
        if distribution is None:
            raise LicenseAuditError(f"RUNTIME_DISTRIBUTION_MISSING:{name}")
        if name != root:
            result.append(distribution)
        for raw_requirement in distribution.requires or ():
            try:
                requirement = Requirement(raw_requirement)
            except InvalidRequirement as exc:
                raise LicenseAuditError(f"RUNTIME_REQUIREMENT_INVALID:{name}") from exc
            if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
                continue
            queue.append(canonicalize_name(requirement.name))
    return tuple(sorted(result, key=lambda item: canonicalize_name(item.metadata["Name"])))


def license_label(distribution: metadata.Distribution) -> str:
    """Return finite package-declared license evidence or fail instead of guessing."""
    expression = (distribution.metadata.get("License-Expression") or "").strip()
    if expression:
        return expression
    declared = _normalize_metadata_text(distribution.metadata.get("License") or "")
    if declared and declared.casefold() not in {"unknown", "n/a", "none"}:
        return declared
    classifiers = tuple(
        value.removeprefix(_LICENSE_CLASSIFIER_PREFIX)
        for value in distribution.metadata.get_all("Classifier", ())
        if value.startswith(_LICENSE_CLASSIFIER_PREFIX)
    )
    if classifiers:
        return "; ".join(sorted(classifiers))
    raise LicenseAuditError(
        f"RUNTIME_LICENSE_UNKNOWN:{canonicalize_name(distribution.metadata['Name'])}"
    )


def render_notices(project_name: str) -> str:
    """Render deterministic Markdown using installed version and license metadata only."""
    rows = []
    for distribution in runtime_distributions(project_name):
        name = distribution.metadata["Name"]
        rows.append(f"| {name} | {distribution.version} | {license_label(distribution)} |")
    return "\n".join(
        (
            "# Third-party notices",
            "",
            "Generated from the locked runtime dependency closure. License labels are",
            "package-declared metadata; this inventory is not legal advice and does not",
            "replace the full license texts shipped by dependencies.",
            "Development/test-only dependency groups are excluded.",
            "",
            "| Package | Locked version | Declared license |",
            "|---|---:|---|",
            *rows,
            "",
        )
    )


def _normalize_metadata_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()[:500]


def main(argv: list[str] | None = None) -> int:
    """Write one explicit output and fail when any runtime license is unknown."""
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--project", default="windows-pc-manager-agent")
    arguments = parser.parse_args(argv)
    arguments.output.write_text(render_notices(arguments.project), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
