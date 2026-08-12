"""One-operation capabilities required before an R1 tool may execute."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from pydantic import Field, JsonValue

from pc_manager_agent.domain.plans import FrozenModel


def arguments_digest(arguments: Mapping[str, object]) -> str:
    """Hash validated JSON-compatible tool arguments in a stable form."""
    payload = json.dumps(dict(arguments), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ExecutionAuthorization(FrozenModel):
    """Internal capability bound to one persisted confirmed transaction item."""

    transaction_id: UUID
    operation_id: UUID
    plan_id: UUID
    preview_id: UUID
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    arguments_digest: str = Field(min_length=64, max_length=64)
    runtime_confirmation_id: UUID | None = None


class WriteExecutionGuard(Protocol):
    """Validate a write capability against durable transaction state."""

    def require(
        self,
        authorization: ExecutionAuthorization,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> None:
        """Raise unless the exact item is currently authorized to execute."""
        ...
