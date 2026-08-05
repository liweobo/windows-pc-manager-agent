"""Validated application settings loaded from process environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_path
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class AppSettings(BaseModel):
    """Runtime settings with safe, non-networked defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    app_name: str = "WindowsPCManagerAgent"
    llm_provider: str = "disabled"
    openai_model: str | None = None
    openai_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    data_directory: Path = Field(
        default_factory=lambda: user_data_path("WindowsPCManagerAgent", ensure_exists=False)
    )
    scan_max_files: int = Field(default=50_000, ge=1, le=100_000)
    scan_timeout_seconds: float = Field(default=300.0, gt=0, le=3_600)
    confirmation_ttl_seconds: int = Field(default=300, ge=30, le=3_600)

    @field_validator("llm_provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        """Allow only deliberately supported provider identifiers."""
        normalized = value.strip().lower()
        if normalized not in {"disabled", "openai"}:
            msg = f"Unsupported LLM provider: {value!r}"
            raise ValueError(msg)
        return normalized

    @field_validator("openai_model")
    @classmethod
    def normalize_optional_model(cls, value: str | None) -> str | None:
        """Treat an empty model string as missing configuration."""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @property
    def database_path(self) -> Path:
        """Return the per-user SQLite path, outside the source repository."""
        return self.data_directory / "state.db"

    @classmethod
    def from_environment(cls) -> AppSettings:
        """Build settings without reading a project-local secret file."""
        raw: dict[str, object] = {
            "llm_provider": os.getenv("PC_MANAGER_LLM_PROVIDER", "disabled"),
            "openai_model": os.getenv("OPENAI_MODEL"),
            "openai_api_key": os.getenv("OPENAI_API_KEY"),
            "scan_max_files": os.getenv("PC_MANAGER_SCAN_MAX_FILES", "50000"),
            "scan_timeout_seconds": os.getenv("PC_MANAGER_SCAN_TIMEOUT_SECONDS", "300"),
        }
        data_directory = os.getenv("PC_MANAGER_DATA_DIRECTORY")
        if data_directory:
            raw["data_directory"] = Path(data_directory)
        return cls.model_validate(raw)
