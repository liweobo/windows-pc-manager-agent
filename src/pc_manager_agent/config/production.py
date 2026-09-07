"""Fail-closed production build mode and immutable release feature policy."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from platformdirs import user_data_path
from pydantic import BaseModel, ConfigDict, Field


class BuildMode(StrEnum):
    """Identify whether configuration belongs to source development, tests, or release."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class ReleaseFeature(StrEnum):
    """Closed set of user-visible domains that a release may expose."""

    FILE_ANALYSIS = "file_analysis"
    FILE_OPERATIONS = "file_operations"
    RECYCLE_BIN = "recycle_bin"
    SYSTEM_DIAGNOSTICS = "system_diagnostics"
    PROCESS_ACTIONS = "process_actions"
    STARTUP_ACTIONS = "startup_actions"
    SERVICE_ACTIONS = "service_actions"
    SOFTWARE_ANALYSIS = "software_analysis"
    SOFTWARE_UNINSTALL = "software_uninstall"
    RESIDUAL_ANALYSIS = "residual_analysis"
    RESIDUAL_CLEANUP = "residual_cleanup"
    PRIVILEGED_BROKER = "privileged_broker"
    OPTIMIZATION_ANALYSIS = "optimization_analysis"
    SYSTEM_CLEANUP = "system_cleanup"
    OFFICE = "office"
    VOICE = "voice"
    BROWSER = "browser"
    MEMORY = "memory"
    MULTI_AGENT = "multi_agent"
    FINAL_ORCHESTRATOR = "final_orchestrator"


class FeatureDisabledError(RuntimeError):
    """Raised before preparation when a build does not expose one domain."""


class FeatureFlags(BaseModel):
    """Immutable exact allow-list; absence means disabled and unknown names cannot parse."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: frozenset[ReleaseFeature] = Field(default_factory=frozenset)

    @classmethod
    def development_defaults(cls) -> FeatureFlags:
        """Expose existing development surfaces while retaining their domain safety layers."""
        return cls(enabled=frozenset(ReleaseFeature))

    @classmethod
    def private_rc_defaults(cls) -> FeatureFlags:
        """Expose only reviewed R0 surfaces in the first unsigned private RC."""
        return cls(
            enabled=frozenset(
                {
                    ReleaseFeature.FILE_ANALYSIS,
                    ReleaseFeature.SYSTEM_DIAGNOSTICS,
                    ReleaseFeature.SOFTWARE_ANALYSIS,
                    ReleaseFeature.OPTIMIZATION_ANALYSIS,
                }
            )
        )

    def is_enabled(self, feature: ReleaseFeature) -> bool:
        """Return whether one exact enumerated domain is enabled."""
        return feature in self.enabled

    def require(self, feature: ReleaseFeature) -> None:
        """Fail before plan preparation when a disabled domain is requested."""
        if not self.is_enabled(feature):
            raise FeatureDisabledError(f"FEATURE_DISABLED:{feature.value}")


class ProductionRuntimeContext(BaseModel):
    """Non-secret process evidence used by deterministic startup validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    frozen_binary: bool
    process_elevated: bool
    executable_path: Path
    active_environment_names: frozenset[str] = Field(default_factory=frozenset)


class ProductionConfigViolation(BaseModel):
    """Stable non-secret reason that prevents production startup."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str


class ProductionConfigReport(BaseModel):
    """Complete startup decision without credential values or local content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    violations: tuple[ProductionConfigViolation, ...]


class ProductionConfigurationError(RuntimeError):
    """Raised when a production process cannot prove its safe configuration."""

    def __init__(self, report: ProductionConfigReport) -> None:
        self.report = report
        codes = ",".join(item.code for item in report.violations)
        super().__init__(f"PRODUCTION_CONFIG_INVALID:{codes}")


class ProductionConfigValidator:
    """Reject development, elevation, unsafe flags, and widened RC capabilities."""

    _BLOCKED_ENVIRONMENT_MARKERS = ("DEBUG", "MOCK", "BYPASS", "UNSAFE", "TEST")

    def validate(
        self, settings: object, context: ProductionRuntimeContext
    ) -> ProductionConfigReport:
        """Validate an AppSettings-compatible object without importing the settings module."""
        violations: list[ProductionConfigViolation] = []
        mode = getattr(settings, "build_mode", None)
        features = getattr(settings, "feature_flags", None)
        if mode is not BuildMode.PRODUCTION:
            violations.append(self._violation("BUILD_MODE", "Production build mode is required"))
        if not context.frozen_binary:
            violations.append(
                self._violation("NOT_FROZEN", "Production must run from a frozen binary")
            )
        if context.process_elevated:
            violations.append(
                self._violation("MAIN_ELEVATED", "The Main Agent must be standard-user")
            )
        if features != FeatureFlags.private_rc_defaults():
            violations.append(
                self._violation(
                    "FEATURE_SET", "Production feature allow-list differs from the RC policy"
                )
            )
        if getattr(settings, "privileged_broker_mode", None) != "disabled":
            violations.append(
                self._violation("BROKER_ENABLED", "Unsigned private RC must keep Broker disabled")
            )
        if getattr(settings, "privileged_broker_trust_mode", None) != "production":
            violations.append(
                self._violation("BROKER_TRUST", "Development Broker trust is forbidden")
            )
        app_name = getattr(settings, "app_name", "WindowsPCManagerAgent")
        expected_data = user_data_path(str(app_name), ensure_exists=False).resolve(strict=False)
        configured_data = getattr(settings, "data_directory", Path()).resolve(strict=False)
        if configured_data != expected_data:
            violations.append(
                self._violation(
                    "DATA_DIRECTORY", "Production requires the fixed per-user data directory"
                )
            )
        provider = getattr(settings, "llm_provider", "disabled")
        if provider == "openai" and (
            getattr(settings, "openai_model", None) is None
            or getattr(settings, "openai_api_key", None) is None
        ):
            violations.append(
                self._violation(
                    "PROVIDER_INCOMPLETE", "Configured provider credentials are incomplete"
                )
            )
        blocked_names = sorted(
            name
            for name in context.active_environment_names
            if name.startswith("PC_MANAGER_")
            and any(marker in name for marker in self._BLOCKED_ENVIRONMENT_MARKERS)
        )
        if blocked_names:
            violations.append(
                self._violation(
                    "DEVELOPMENT_ENVIRONMENT",
                    "Production environment contains a blocked development setting name",
                )
            )
        return ProductionConfigReport(valid=not violations, violations=tuple(violations))

    def require_valid(self, settings: object, context: ProductionRuntimeContext) -> None:
        """Raise one stable error when any production invariant is unavailable."""
        report = self.validate(settings, context)
        if not report.valid:
            raise ProductionConfigurationError(report)

    @staticmethod
    def _violation(code: str, message: str) -> ProductionConfigViolation:
        return ProductionConfigViolation(code=code, message=message)
