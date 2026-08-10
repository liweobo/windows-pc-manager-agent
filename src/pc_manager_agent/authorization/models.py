"""Models for user-approved and user-forbidden directories."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class AuthorizedPathKind(StrEnum):
    """Whether a stored root grants or denies scanner access."""

    AUTHORIZED = "authorized"
    FORBIDDEN = "forbidden"


class AuthorizedPath(BaseModel):
    """One immutable local path decision made explicitly by the user."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path_id: UUID = Field(default_factory=uuid4)
    path: Path
    label: str = Field(min_length=1, max_length=120)
    kind: AuthorizedPathKind
    favorite: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
