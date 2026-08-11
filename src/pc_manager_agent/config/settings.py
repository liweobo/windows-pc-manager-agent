"""Validated application settings loaded from process environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_path
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class AppSettings(BaseModel):
    """Runtime settings with safe defaults; BaseModel validates interaction data."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    # extra="forbid" 不允许传入未定义字段; frozen=True 使实例创建后不可修改.

    app_name: str = "WindowsPCManagerAgent"
    llm_provider: str = "disabled"
    openai_model: str | None = None
    openai_api_key: SecretStr | None = Field(
        default=None, exclude=True, repr=False
    )  # 导出模型时排除 openai_api_key 字段.
    data_directory: Path = Field(
        default_factory=lambda: user_data_path("WindowsPCManagerAgent", ensure_exists=False)
    )  # 返回操作系统官方设计的用户专属数据存储目录路径
    scan_max_files: int = Field(default=50_000, ge=1, le=100_000)
    scan_timeout_seconds: float = Field(default=300.0, gt=0, le=3_600)
    analysis_batch_size: int = Field(default=250, ge=10, le=2_000)
    confirmation_ttl_seconds: int = Field(default=300, ge=30, le=3_600)
    operation_max_objects: int = Field(default=500, ge=1, le=5_000)
    operation_max_total_bytes: int = Field(default=50 * 1024**3, ge=1)

    @field_validator("llm_provider")  # field_validator 校验 llm_provider 字段.
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

    @property  # 将方法转化为只读属性的属性
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
            "analysis_batch_size": os.getenv("PC_MANAGER_ANALYSIS_BATCH_SIZE", "250"),
            "operation_max_objects": os.getenv("PC_MANAGER_OPERATION_MAX_OBJECTS", "500"),
            "operation_max_total_bytes": os.getenv(
                "PC_MANAGER_OPERATION_MAX_TOTAL_BYTES", str(50 * 1024**3)
            ),
        }
        data_directory = os.getenv("PC_MANAGER_DATA_DIRECTORY")
        if data_directory:
            raw["data_directory"] = Path(data_directory)
        return cls.model_validate(raw)  # 按字段类型校验并转换 raw, 然后返回 Pydantic 模型.
