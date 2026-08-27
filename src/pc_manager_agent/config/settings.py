"""Validated application settings loaded from process environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_path
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


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
    trash_max_selected: int = Field(default=100, ge=1, le=500)
    trash_max_contained_objects: int = Field(default=10_000, ge=1, le=100_000)
    trash_max_total_bytes: int = Field(default=50 * 1024**3, ge=1)
    trash_high_impact_objects: int = Field(default=100, ge=1)
    trash_high_impact_bytes: int = Field(default=10 * 1024**3, ge=1)
    trash_runtime_confirmation_ttl_seconds: int = Field(default=60, ge=15, le=300)
    diagnostic_sample_count: int = Field(default=3, ge=2, le=10)
    diagnostic_sample_interval_seconds: float = Field(default=1.5, ge=0.1, le=2.0)
    diagnostic_max_processes: int = Field(default=500, ge=1, le=2_000)
    diagnostic_max_items: int = Field(default=5_000, ge=1, le=20_000)
    process_action_max_applications: int = Field(default=5, ge=1, le=10)
    process_action_max_processes: int = Field(default=20, ge=1, le=50)
    process_graceful_timeout_seconds: float = Field(default=10.0, ge=5.0, le=30.0)
    process_force_timeout_seconds: float = Field(default=10.0, ge=2.0, le=30.0)
    process_runtime_confirmation_ttl_seconds: int = Field(default=60, ge=15, le=300)
    startup_runtime_confirmation_ttl_seconds: int = Field(default=60, ge=15, le=300)
    service_runtime_confirmation_ttl_seconds: int = Field(default=60, ge=15, le=300)
    service_action_timeout_seconds: float = Field(default=30.0, ge=5.0, le=120.0)
    msi_runtime_confirmation_ttl_seconds: int = Field(default=60, ge=15, le=300)
    msi_monitor_poll_seconds: float = Field(default=0.25, ge=0.05, le=5.0)
    msi_long_running_seconds: float = Field(default=900.0, ge=30.0, le=7_200.0)
    vendor_runtime_confirmation_ttl_seconds: int = Field(default=60, ge=15, le=300)
    vendor_monitor_poll_seconds: float = Field(default=0.25, ge=0.05, le=5.0)
    vendor_long_running_seconds: float = Field(default=900.0, ge=30.0, le=7_200.0)
    winget_runtime_confirmation_ttl_seconds: int = Field(default=60, ge=15, le=300)
    winget_monitor_poll_seconds: float = Field(default=0.25, ge=0.05, le=5.0)
    residual_max_roots: int = Field(default=16, ge=1, le=32)
    residual_max_objects: int = Field(default=25_000, ge=1, le=25_000)
    residual_timeout_seconds: float = Field(default=60.0, gt=0, le=600.0)
    residual_cleanup_max_selected: int = Field(default=20, ge=1, le=100)
    residual_cleanup_max_contained_objects: int = Field(default=10_000, ge=1, le=100_000)
    residual_cleanup_max_total_bytes: int = Field(default=50 * 1024**3, ge=1)
    residual_cleanup_normal_item_count: int = Field(default=5, ge=1, le=100)
    residual_cleanup_normal_object_count: int = Field(default=100, ge=1)
    residual_cleanup_normal_total_bytes: int = Field(default=1 * 1024**3, ge=1)
    residual_cleanup_normal_single_item_bytes: int = Field(default=512 * 1024**2, ge=1)
    residual_cleanup_runtime_confirmation_ttl_seconds: int = Field(default=60, ge=15, le=300)
    privileged_broker_mode: str = "disabled"
    privileged_request_ttl_seconds: int = Field(default=120, ge=15, le=600)
    privileged_runtime_confirmation_ttl_seconds: int = Field(default=60, ge=15, le=300)
    privileged_max_request_bytes: int = Field(default=32_768, ge=1_024, le=1_048_576)
    privileged_broker_path: Path | None = None
    privileged_broker_expected_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    privileged_broker_trust_mode: str = "production"
    privileged_broker_connect_timeout_seconds: float = Field(default=30.0, ge=5.0, le=120.0)
    privileged_broker_message_timeout_seconds: float = Field(default=15.0, ge=2.0, le=60.0)

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

    @field_validator("privileged_broker_mode")
    @classmethod
    def validate_privileged_broker_mode(cls, value: str) -> str:
        """Allow disabled, visibly Mock, or explicit one-shot Windows behavior."""
        normalized = value.strip().lower()
        if normalized not in {"disabled", "mock", "windows"}:
            raise ValueError("Privileged broker mode must be disabled, mock, or windows")
        return normalized

    @field_validator("privileged_broker_trust_mode")
    @classmethod
    def validate_privileged_broker_trust_mode(cls, value: str) -> str:
        """Permit only production signing or explicit development hash policy."""
        normalized = value.strip().lower()
        if normalized not in {"production", "development"}:
            raise ValueError("Broker trust mode must be production or development")
        return normalized

    @model_validator(mode="after")
    def validate_windows_broker_configuration(self) -> AppSettings:
        """Fail before UAC when Broker trust or the fixed database path is ambiguous."""
        if self.privileged_broker_mode == "windows":
            if self.privileged_broker_path is None or not self.privileged_broker_path.is_absolute():
                raise ValueError("Windows Broker mode requires an absolute Broker path")
            if self.privileged_broker_expected_sha256 is None:
                raise ValueError("Windows Broker mode requires an expected SHA-256")
            fixed_data_directory = user_data_path(self.app_name, ensure_exists=False)
            if self.data_directory.resolve(strict=False) != fixed_data_directory.resolve(
                strict=False
            ):
                raise ValueError("Windows Broker mode requires the fixed per-user data directory")
        return self

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
            "trash_max_selected": os.getenv("PC_MANAGER_TRASH_MAX_SELECTED", "100"),
            "trash_max_contained_objects": os.getenv(
                "PC_MANAGER_TRASH_MAX_CONTAINED_OBJECTS", "10000"
            ),
            "trash_max_total_bytes": os.getenv(
                "PC_MANAGER_TRASH_MAX_TOTAL_BYTES", str(50 * 1024**3)
            ),
            "trash_high_impact_objects": os.getenv("PC_MANAGER_TRASH_HIGH_IMPACT_OBJECTS", "100"),
            "trash_high_impact_bytes": os.getenv(
                "PC_MANAGER_TRASH_HIGH_IMPACT_BYTES", str(10 * 1024**3)
            ),
            "trash_runtime_confirmation_ttl_seconds": os.getenv(
                "PC_MANAGER_TRASH_RUNTIME_CONFIRMATION_TTL_SECONDS", "60"
            ),
            "diagnostic_sample_count": os.getenv("PC_MANAGER_DIAGNOSTIC_SAMPLE_COUNT", "3"),
            "diagnostic_sample_interval_seconds": os.getenv(
                "PC_MANAGER_DIAGNOSTIC_SAMPLE_INTERVAL_SECONDS", "1.5"
            ),
            "diagnostic_max_processes": os.getenv("PC_MANAGER_DIAGNOSTIC_MAX_PROCESSES", "500"),
            "diagnostic_max_items": os.getenv("PC_MANAGER_DIAGNOSTIC_MAX_ITEMS", "5000"),
            "process_action_max_applications": os.getenv(
                "PC_MANAGER_PROCESS_ACTION_MAX_APPLICATIONS", "5"
            ),
            "process_action_max_processes": os.getenv(
                "PC_MANAGER_PROCESS_ACTION_MAX_PROCESSES", "20"
            ),
            "process_graceful_timeout_seconds": os.getenv(
                "PC_MANAGER_PROCESS_GRACEFUL_TIMEOUT_SECONDS", "10"
            ),
            "process_force_timeout_seconds": os.getenv(
                "PC_MANAGER_PROCESS_FORCE_TIMEOUT_SECONDS", "10"
            ),
            "process_runtime_confirmation_ttl_seconds": os.getenv(
                "PC_MANAGER_PROCESS_RUNTIME_CONFIRMATION_TTL_SECONDS", "60"
            ),
            "startup_runtime_confirmation_ttl_seconds": os.getenv(
                "PC_MANAGER_STARTUP_RUNTIME_CONFIRMATION_TTL_SECONDS", "60"
            ),
            "service_runtime_confirmation_ttl_seconds": os.getenv(
                "PC_MANAGER_SERVICE_RUNTIME_CONFIRMATION_TTL_SECONDS", "60"
            ),
            "service_action_timeout_seconds": os.getenv(
                "PC_MANAGER_SERVICE_ACTION_TIMEOUT_SECONDS", "30"
            ),
            "msi_runtime_confirmation_ttl_seconds": os.getenv(
                "PC_MANAGER_MSI_RUNTIME_CONFIRMATION_TTL_SECONDS", "60"
            ),
            "msi_monitor_poll_seconds": os.getenv("PC_MANAGER_MSI_MONITOR_POLL_SECONDS", "0.25"),
            "msi_long_running_seconds": os.getenv("PC_MANAGER_MSI_LONG_RUNNING_SECONDS", "900"),
            "vendor_runtime_confirmation_ttl_seconds": os.getenv(
                "PC_MANAGER_VENDOR_RUNTIME_CONFIRMATION_TTL_SECONDS", "60"
            ),
            "vendor_monitor_poll_seconds": os.getenv(
                "PC_MANAGER_VENDOR_MONITOR_POLL_SECONDS", "0.25"
            ),
            "vendor_long_running_seconds": os.getenv(
                "PC_MANAGER_VENDOR_LONG_RUNNING_SECONDS", "900"
            ),
            "winget_runtime_confirmation_ttl_seconds": os.getenv(
                "PC_MANAGER_WINGET_RUNTIME_CONFIRMATION_TTL_SECONDS", "60"
            ),
            "winget_monitor_poll_seconds": os.getenv(
                "PC_MANAGER_WINGET_MONITOR_POLL_SECONDS", "0.25"
            ),
            "residual_max_roots": os.getenv("PC_MANAGER_RESIDUAL_MAX_ROOTS", "16"),
            "residual_max_objects": os.getenv("PC_MANAGER_RESIDUAL_MAX_OBJECTS", "25000"),
            "residual_timeout_seconds": os.getenv("PC_MANAGER_RESIDUAL_TIMEOUT_SECONDS", "60"),
            "residual_cleanup_max_selected": os.getenv(
                "PC_MANAGER_RESIDUAL_CLEANUP_MAX_SELECTED", "20"
            ),
            "residual_cleanup_max_contained_objects": os.getenv(
                "PC_MANAGER_RESIDUAL_CLEANUP_MAX_CONTAINED_OBJECTS", "10000"
            ),
            "residual_cleanup_max_total_bytes": os.getenv(
                "PC_MANAGER_RESIDUAL_CLEANUP_MAX_TOTAL_BYTES", str(50 * 1024**3)
            ),
            "residual_cleanup_normal_item_count": os.getenv(
                "PC_MANAGER_RESIDUAL_CLEANUP_NORMAL_ITEM_COUNT", "5"
            ),
            "residual_cleanup_normal_object_count": os.getenv(
                "PC_MANAGER_RESIDUAL_CLEANUP_NORMAL_OBJECT_COUNT", "100"
            ),
            "residual_cleanup_normal_total_bytes": os.getenv(
                "PC_MANAGER_RESIDUAL_CLEANUP_NORMAL_TOTAL_BYTES", str(1 * 1024**3)
            ),
            "residual_cleanup_normal_single_item_bytes": os.getenv(
                "PC_MANAGER_RESIDUAL_CLEANUP_NORMAL_SINGLE_ITEM_BYTES", str(512 * 1024**2)
            ),
            "residual_cleanup_runtime_confirmation_ttl_seconds": os.getenv(
                "PC_MANAGER_RESIDUAL_CLEANUP_RUNTIME_CONFIRMATION_TTL_SECONDS", "60"
            ),
            "privileged_broker_mode": os.getenv("PC_MANAGER_PRIVILEGED_BROKER_MODE", "disabled"),
            "privileged_request_ttl_seconds": os.getenv(
                "PC_MANAGER_PRIVILEGED_REQUEST_TTL_SECONDS", "120"
            ),
            "privileged_runtime_confirmation_ttl_seconds": os.getenv(
                "PC_MANAGER_PRIVILEGED_RUNTIME_CONFIRMATION_TTL_SECONDS", "60"
            ),
            "privileged_max_request_bytes": os.getenv(
                "PC_MANAGER_PRIVILEGED_MAX_REQUEST_BYTES", "32768"
            ),
            "privileged_broker_path": os.getenv("PC_MANAGER_PRIVILEGED_BROKER_PATH"),
            "privileged_broker_expected_sha256": os.getenv(
                "PC_MANAGER_PRIVILEGED_BROKER_EXPECTED_SHA256"
            ),
            "privileged_broker_trust_mode": os.getenv(
                "PC_MANAGER_PRIVILEGED_BROKER_TRUST_MODE", "production"
            ),
            "privileged_broker_connect_timeout_seconds": os.getenv(
                "PC_MANAGER_PRIVILEGED_BROKER_CONNECT_TIMEOUT_SECONDS", "30"
            ),
            "privileged_broker_message_timeout_seconds": os.getenv(
                "PC_MANAGER_PRIVILEGED_BROKER_MESSAGE_TIMEOUT_SECONDS", "15"
            ),
        }
        data_directory = os.getenv("PC_MANAGER_DATA_DIRECTORY")
        if data_directory:
            raw["data_directory"] = Path(data_directory)
        return cls.model_validate(raw)  # 按字段类型校验并转换 raw, 然后返回 Pydantic 模型.
