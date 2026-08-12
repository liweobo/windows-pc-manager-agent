"""Structured tool manifests and cancellation primitives."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_.-]+$")


class CancellationToken:
    """Thread-safe cooperative cancellation signal."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation."""
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        """Return whether cancellation was requested."""
        return self._event.is_set()

    def cancellation_requested(self) -> bool:
        """Read cancellation dynamically for long-running worker loops."""
        return self._event.is_set()


@dataclass(frozen=True, slots=True)
class ToolManifest:
    """Deterministic metadata required before a tool can be registered."""

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    risk_level: RiskLevel
    required_permissions: tuple[str, ...]
    read_only: bool
    idempotent: bool
    supports_cancellation: bool
    rollback_level: RollbackLevel
    preconditions: tuple[str, ...]
    postconditions: tuple[str, ...]
    timeout_seconds: float
    max_batch_size: int
    audit_fields: tuple[str, ...]
    supported_platforms: tuple[str, ...]
    requires_confirmation: bool = True
    requires_runtime_confirmation: bool = False
    supports_preview: bool = False
    scope_argument_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject incomplete or contradictory manifests at registration time."""
        if not _TOOL_NAME.fullmatch(self.name):
            msg = f"Invalid tool name: {self.name!r}"
            raise ValueError(msg)
        if not self.description.strip():
            msg = "Tool description is required"
            raise ValueError(msg)
        if self.timeout_seconds <= 0 or self.max_batch_size <= 0:
            msg = "Tool timeout and batch size must be positive"
            raise ValueError(msg)
        if self.risk_level is RiskLevel.R0 and not self.read_only:
            msg = "R0 tools must be read-only"
            raise ValueError(msg)
        if not self.read_only and not self.requires_confirmation:
            raise ValueError("Write tools must require confirmation")
        if self.risk_level is RiskLevel.R2 and not self.requires_runtime_confirmation:
            raise ValueError("R2 tools must require immediate runtime confirmation")
        if self.requires_runtime_confirmation and self.risk_level is not RiskLevel.R2:
            raise ValueError("Immediate runtime confirmation is reserved for R2 tools")
        if not self.read_only and not self.supports_preview:
            raise ValueError("Write tools must support Preview")
        if not self.read_only and self.rollback_level is RollbackLevel.NONE:
            raise ValueError("Write tools must declare a rollback capability")


class RegisteredTool(Protocol):
    """Executable deterministic tool contract."""

    @property
    def manifest(self) -> ToolManifest:
        """Return immutable tool metadata."""
        ...

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Execute validated arguments and return the declared output model."""
        ...
