"""Evaluate explicit release evidence without inferring skipped checks as success."""

from __future__ import annotations

import argparse

from pc_manager_agent.release.gate import (
    EvidenceStatus,
    ReadinessLevel,
    ReleaseCheck,
    ReleaseEvidence,
    ReleaseGateEvaluator,
)


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse only the closed readiness, check, status and reference vocabularies."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--requested", required=True, choices=tuple(ReadinessLevel))
    parser.add_argument("--reference", required=True)
    for status in EvidenceStatus:
        parser.add_argument(
            f"--{status.value.casefold().replace('_', '-')}",
            nargs="*",
            default=(),
            choices=tuple(ReleaseCheck),
        )
    return parser.parse_args(argv)


def evidence_from_arguments(arguments: argparse.Namespace) -> tuple[ReleaseEvidence, ...]:
    """Build exact evidence; duplicates remain visible for the evaluator to reject."""
    evidence: list[ReleaseEvidence] = []
    for status in EvidenceStatus:
        values = getattr(arguments, status.value.casefold())
        evidence.extend(
            ReleaseEvidence(
                check=ReleaseCheck(value),
                status=status,
                reference=f"{arguments.reference}:{value}",
            )
            for value in values
        )
    return tuple(evidence)


def main(argv: list[str] | None = None) -> int:
    """Print a machine-readable decision and fail the process unless the request passes."""
    arguments = parse_arguments(argv)
    result = ReleaseGateEvaluator().evaluate(
        ReadinessLevel(arguments.requested),
        evidence_from_arguments(arguments),
    )
    print(result.model_dump_json(indent=2))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
