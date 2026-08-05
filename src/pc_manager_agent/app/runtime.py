"""Dependency composition for GUI and headless tests."""

from __future__ import annotations

from pathlib import Path

from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.confirmation.state_machine import ConfirmationService
from pc_manager_agent.orchestration.service import ScanOrchestrator
from pc_manager_agent.providers.llm.base import LLMProvider
from pc_manager_agent.providers.llm.openai_provider import OpenAILLMProvider
from pc_manager_agent.safety.path_policy import PathPolicy
from pc_manager_agent.safety.plan_reviewer import SafetyReviewer
from pc_manager_agent.tools.file_tools.scanner import DirectoryScannerTool
from pc_manager_agent.tools.registry import ToolRegistry


class ProviderConfigurationError(RuntimeError):
    """Raised when a requested model provider lacks explicit safe configuration."""


class ApplicationRuntime:
    """Own shared infrastructure and create root-scoped orchestrators."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.audit = AuditRepository(settings.database_path)
        self.audit.initialize()
        self.confirmation = ConfirmationService(settings.confirmation_ttl_seconds)

    def create_scan_orchestrator(self, root: Path) -> ScanOrchestrator:
        """Create a new registry and reviewer limited to exactly one user-selected root."""
        policy = PathPolicy.for_scan_root(root)
        registry = ToolRegistry()
        registry.register(DirectoryScannerTool(policy))
        reviewer = SafetyReviewer(registry, policy)
        return ScanOrchestrator(
            registry=registry,
            reviewer=reviewer,
            path_policy=policy,
            confirmation=self.confirmation,
            audit=self.audit,
            max_files=self.settings.scan_max_files,
            timeout_seconds=self.settings.scan_timeout_seconds,
        )

    def create_llm_provider(self) -> LLMProvider | None:
        """Build an explicitly configured provider; disabled is the safe default."""
        if self.settings.llm_provider == "disabled":
            return None
        if self.settings.llm_provider == "openai":
            if self.settings.openai_model is None or self.settings.openai_api_key is None:
                raise ProviderConfigurationError(
                    "OpenAI requires OPENAI_MODEL and OPENAI_API_KEY environment variables"
                )
            return OpenAILLMProvider(
                model=self.settings.openai_model,
                api_key=self.settings.openai_api_key.get_secret_value(),
            )
        raise ProviderConfigurationError("Unsupported LLM provider")

    def close(self) -> None:
        """Release local persistence resources."""
        self.audit.close()
