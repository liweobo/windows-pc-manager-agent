"""Resolve user semantics to fresh, unambiguous process identities."""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from pathlib import Path

from pc_manager_agent.domain.process_actions import (
    ProcessActionErrorCode,
    ProcessObservation,
    ProcessTargetQuery,
    ProcessTargetQueryType,
    ResolvedProcessTarget,
)
from pc_manager_agent.domain.process_errors import ProcessTargetResolutionError
from pc_manager_agent.platform_support.processes import ProcessManagementPlatform


class ProcessTargetResolver:
    """Resolve PID/name selections locally; stale Stage 3 data never grants authority."""

    def __init__(self, platform: ProcessManagementPlatform, max_processes: int = 2_000) -> None:
        self._platform = platform
        self._max_processes = max_processes

    def resolve(self, query: ProcessTargetQuery) -> tuple[ResolvedProcessTarget, ...]:
        """Refresh current state and either return exact targets or fail on ambiguity."""
        if query.query_type in {
            ProcessTargetQueryType.PID,
            ProcessTargetQueryType.SELECTED_PROCESS,
        }:
            if query.pid is None:
                raise ProcessTargetResolutionError(
                    ProcessActionErrorCode.TARGET_NOT_FOUND,
                    "A PID query reached the resolver without a PID",
                )
            selected = self._platform.inspect_process(query.pid)
            if selected is None:
                raise ProcessTargetResolutionError(
                    ProcessActionErrorCode.TARGET_NOT_FOUND,
                    "The selected process no longer exists or cannot be identified safely",
                )
            if query.include_application_group:
                candidates = self._platform.list_processes(self._max_processes)
                members = tuple(
                    item
                    for item in candidates
                    if _application_key(item) == _application_key(selected)
                )
                if not members:
                    members = (selected,)
            else:
                members = (selected,)
            return (_target(members),)

        if query.text is None:
            raise ProcessTargetResolutionError(
                ProcessActionErrorCode.TARGET_NOT_FOUND,
                "A name query reached the resolver without a process name",
            )
        normalized = _normalize_query(query.text)
        matches = tuple(
            item
            for item in self._platform.list_processes(self._max_processes)
            if _matches_name(item, normalized)
        )
        if not matches:
            raise ProcessTargetResolutionError(
                ProcessActionErrorCode.TARGET_NOT_FOUND,
                f"No current process unambiguously matches {query.text!r}",
            )
        groups: dict[str, list[ProcessObservation]] = defaultdict(list)
        for match in matches:
            groups[_application_key(match)].append(match)
        if len(groups) != 1:
            labels = sorted({item.identity.executable_path.name for item in matches})
            raise ProcessTargetResolutionError(
                ProcessActionErrorCode.TARGET_AMBIGUOUS,
                "Multiple distinct applications match; choose a specific process: "
                + ", ".join(labels[:10]),
            )
        return (_target(tuple(next(iter(groups.values())))),)

    def re_resolve(self, target: ResolvedProcessTarget) -> ResolvedProcessTarget:
        """Refresh every member and reject application-group membership changes."""
        current_all = self._platform.list_processes(self._max_processes)
        current = tuple(
            item for item in current_all if _application_key(item) == target.application_group_key
        )
        if not current:
            raise ProcessTargetResolutionError(
                ProcessActionErrorCode.TARGET_NOT_FOUND,
                "The target application has already exited",
            )
        refreshed = _target(current)
        if refreshed.identity_set_digest() != target.identity_set_digest():
            raise ProcessTargetResolutionError(
                ProcessActionErrorCode.TARGET_GROUP_CHANGED,
                "Application group membership changed; generate a new Preview",
            )
        return refreshed

    def resolve_current_application_group(
        self,
        target: ResolvedProcessTarget,
    ) -> ResolvedProcessTarget | None:
        """Resolve the currently remaining members of a previously shown application."""
        current = tuple(
            item
            for item in self._platform.list_processes(self._max_processes)
            if _application_key(item) == target.application_group_key
        )
        return _target(current) if current else None

    def inspect_pid(self, pid: int) -> ProcessObservation | None:
        """Expose one fresh complete observation for execution-time identity checks."""
        return self._platform.inspect_process(pid)


def _target(members: tuple[ProcessObservation, ...]) -> ResolvedProcessTarget:
    ordered = tuple(sorted(members, key=lambda item: item.identity.pid))
    first = ordered[0]
    display_name = first.identity.executable_path.stem or first.identity.process_name
    return ResolvedProcessTarget(
        display_name=display_name,
        application_group_key=_application_key(first),
        members=ordered,
    )


def _application_key(observation: ProcessObservation) -> str:
    value = os.path.normcase(str(observation.identity.executable_path.resolve(strict=False)))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_query(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized.endswith(".exe"):
        normalized = normalized[:-4]
    return normalized


def _matches_name(observation: ProcessObservation, query: str) -> bool:
    identity = observation.identity
    candidates = {
        identity.process_name.casefold(),
        Path(identity.process_name).stem.casefold(),
        identity.executable_path.name.casefold(),
        identity.executable_path.stem.casefold(),
    }
    return query in candidates
