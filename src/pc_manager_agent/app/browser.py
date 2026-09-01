"""Dependency composition for the isolated Stage 5C browser workflow."""

from __future__ import annotations

from dataclasses import dataclass

from pc_manager_agent.audit.browser import BrowserAuditLogger
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.browser.adapter import BrowserAdapter
from pc_manager_agent.browser.client import BrowserWorkerClient
from pc_manager_agent.config.browser import BrowserSecuritySettings
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.confirmation.browser import BrowserConfirmationService
from pc_manager_agent.orchestration.browser import BrowserPlanCompiler, BrowserTaskService
from pc_manager_agent.persistence.browser import (
    BrowserConfirmationRepository,
    BrowserWriteExecutionGuard,
)
from pc_manager_agent.safety.browser.actions import BrowserActionPolicy
from pc_manager_agent.safety.browser.downloads import BrowserDownloadManager, BrowserDownloadPolicy
from pc_manager_agent.safety.browser.network import BrowserUrlPolicy
from pc_manager_agent.tools.browser_tools import build_browser_registry
from pc_manager_agent.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class BrowserServices:
    """Stage 5C components exposed to GUI and headless tests."""

    registry: ToolRegistry
    service: BrowserTaskService
    confirmation_repository: BrowserConfirmationRepository

    def close(self) -> None:
        """Close disposable browser state and confirmation storage."""
        self.service.close()
        self.confirmation_repository.close()


def build_browser_services(
    settings: AppSettings,
    audit_repository: AuditRepository,
    *,
    adapter: BrowserAdapter | None = None,
    security_settings: BrowserSecuritySettings | None = None,
) -> BrowserServices:
    """Build one isolated browser registry and service without a model or Broker dependency."""
    limits = security_settings or BrowserSecuritySettings()
    browser_adapter = adapter or BrowserWorkerClient(
        timeout_seconds=limits.navigation_timeout_seconds + 15.0
    )
    url_policy = BrowserUrlPolicy()
    action_policy = BrowserActionPolicy()
    download_policy = BrowserDownloadPolicy()
    confirmation_repository = BrowserConfirmationRepository(settings.database_path)
    confirmation_repository.initialize()
    write_guard = BrowserWriteExecutionGuard()
    registry = build_browser_registry(browser_adapter, write_guard=write_guard)
    service = BrowserTaskService(
        registry=registry,
        adapter=browser_adapter,
        compiler=BrowserPlanCompiler(url_policy, action_policy),
        confirmation=BrowserConfirmationService(
            confirmation_repository,
            limits.confirmation_ttl_seconds,
        ),
        confirmation_repository=confirmation_repository,
        write_guard=write_guard,
        audit=BrowserAuditLogger(audit_repository),
        temporary_directory=settings.data_directory / "browser" / "temporary-downloads",
        settings=limits,
        url_policy=url_policy,
        action_policy=action_policy,
        download_policy=download_policy,
        download_manager=BrowserDownloadManager(download_policy),
    )
    return BrowserServices(
        registry=registry,
        service=service,
        confirmation_repository=confirmation_repository,
    )
