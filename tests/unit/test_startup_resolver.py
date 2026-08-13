"""Exact-name startup target resolution and ambiguity tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.domain.startup_actions import StartupObservation
from pc_manager_agent.domain.startup_errors import StartupActionError
from pc_manager_agent.orchestration.startup_target_resolver import StartupTargetResolver
from tests.unit.test_startup_actions import observation


class ResolverPlatform:
    def __init__(self, values: tuple[StartupObservation, ...]) -> None:
        self.values = values

    def list_entries(self, max_items: int = 5_000) -> tuple[StartupObservation, ...]:
        return self.values[:max_items]

    def inspect(self, identity):  # type: ignore[no-untyped-def]
        return next((value for value in self.values if value.identity == identity), None)

    def capture_backup(self, identity, backup_id):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def disable(self, payload):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def restore(self, payload):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def disabled_material_matches(self, payload):  # type: ignore[no-untyped-def]
        raise NotImplementedError


def test_resolver_requires_exact_unambiguous_display_name(tmp_path: Path) -> None:
    executable = tmp_path / "one.exe"
    executable.write_bytes(b"MZ")
    one = observation(executable)
    resolver = StartupTargetResolver(ResolverPlatform((one,)))

    assert resolver.resolve_name(" example ") == one
    with pytest.raises(StartupActionError, match="exact name"):
        resolver.resolve_name("exam")


def test_resolver_rejects_same_name_from_multiple_sources(tmp_path: Path) -> None:
    executable = tmp_path / "one.exe"
    executable.write_bytes(b"MZ")
    one = observation(executable)
    two = one.model_copy(update={"command_summary": "same name, distinct local source"})
    resolver = StartupTargetResolver(ResolverPlatform((one, two)))

    with pytest.raises(StartupActionError, match="Multiple startup sources"):
        resolver.resolve_name("Example")
