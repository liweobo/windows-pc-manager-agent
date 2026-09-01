from pathlib import Path

import pytest

from pc_manager_agent.app.browser import BrowserServices
from pc_manager_agent.audit.browser import BrowserAuditLogger
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.browser.fake import FakeBrowserAdapter
from pc_manager_agent.config.browser import BrowserSecuritySettings
from pc_manager_agent.confirmation.browser import BrowserConfirmationService
from pc_manager_agent.domain.browser import BrowserActionKind
from pc_manager_agent.orchestration.browser import BrowserPlanCompiler, BrowserTaskService
from pc_manager_agent.persistence.browser import (
    BrowserAuthorityError,
    BrowserConfirmationRepository,
    BrowserWriteExecutionGuard,
)
from pc_manager_agent.safety.browser.actions import BrowserActionPolicy
from pc_manager_agent.safety.browser.downloads import BrowserDownloadManager, BrowserDownloadPolicy
from pc_manager_agent.safety.browser.network import BrowserUrlPolicy
from pc_manager_agent.tools.browser_tools import build_browser_registry


class _PublicResolver:
    def resolve(self, hostname: str) -> tuple[str, ...]:
        assert hostname == "example.com"
        return ("93.184.216.34",)


def _services(tmp_path: Path) -> tuple[BrowserServices, AuditRepository]:
    adapter = FakeBrowserAdapter(
        {
            "https://example.com/": ("Home", "Visible safe content"),
            "https://example.com/document.pdf": ("Document", "Downloaded report"),
        }
    )
    url_policy = BrowserUrlPolicy(_PublicResolver())
    action_policy = BrowserActionPolicy()
    confirmation_repository = BrowserConfirmationRepository(tmp_path / "state.db")
    confirmation_repository.initialize()
    audit_repository = AuditRepository(tmp_path / "state.db")
    audit_repository.initialize()
    write_guard = BrowserWriteExecutionGuard()
    download_policy = BrowserDownloadPolicy()
    registry = build_browser_registry(adapter, write_guard=write_guard)
    service = BrowserTaskService(
        registry=registry,
        adapter=adapter,
        compiler=BrowserPlanCompiler(url_policy, action_policy),
        confirmation=BrowserConfirmationService(confirmation_repository, 60),
        confirmation_repository=confirmation_repository,
        write_guard=write_guard,
        audit=BrowserAuditLogger(audit_repository),
        temporary_directory=tmp_path / "temporary",
        settings=BrowserSecuritySettings(max_download_bytes=1024),
        url_policy=url_policy,
        action_policy=action_policy,
        download_policy=download_policy,
        download_manager=BrowserDownloadManager(download_policy),
    )
    return (
        BrowserServices(
            registry=registry,
            service=service,
            confirmation_repository=confirmation_repository,
        ),
        audit_repository,
    )


def _confirm(service: BrowserTaskService, plan: object) -> object:
    from pc_manager_agent.domain.browser_plans import BrowserTaskPlan

    assert isinstance(plan, BrowserTaskPlan)
    confirmation = service.request_confirmation(plan)
    service.resolve_confirmation(plan, confirmation.confirmation_id, approved=True)
    return confirmation


def test_navigation_download_recovery_and_semantic_navigation(tmp_path: Path) -> None:
    services, audit = _services(tmp_path)
    service = services.service
    session = service.start(headless=True)
    plan = service.prepare_navigation(
        "https://example.com/",
        user_goal_summary="Read a synthetic page",
    )
    confirmation = _confirm(service, plan)
    result = service.execute(plan, confirmation.confirmation_id)  # type: ignore[attr-defined]
    assert result.observation is not None
    assert result.observation.visible_text == "Visible safe content"
    element = result.observation.elements[0]

    destination = tmp_path / "downloads"
    destination.mkdir()
    preview, download_plan = service.prepare_download(
        element_id=element.element_id,
        destination_directory=destination,
        user_goal_summary="Download one report",
    )
    download_confirmation = _confirm(service, download_plan)
    identity, recovery = service.execute_download(
        preview,
        download_plan,
        download_confirmation.confirmation_id,  # type: ignore[attr-defined]
    )
    assert identity.path.exists()
    assert identity.detected_mime_type == "application/pdf"
    recovered = service.rollback_download(recovery)
    assert recovered.exists()
    assert not identity.path.exists()

    link_plan = service.prepare_action(
        element_id=element.element_id,
        kind=BrowserActionKind.OPEN_LINK,
        user_goal_summary="Open selected result",
    )
    link_confirmation = _confirm(service, link_plan)
    link_result = service.execute(
        link_plan,
        link_confirmation.confirmation_id,  # type: ignore[attr-defined]
    )
    assert link_result.observation is not None
    assert link_result.observation.url.endswith("document.pdf")
    assert service.session == session
    services.close()
    audit.close()


def test_consumed_navigation_confirmation_cannot_replay(tmp_path: Path) -> None:
    services, audit = _services(tmp_path)
    service = services.service
    service.start(headless=True)
    plan = service.prepare_navigation("https://example.com/", user_goal_summary="Read")
    confirmation = _confirm(service, plan)
    service.execute(plan, confirmation.confirmation_id)  # type: ignore[attr-defined]
    with pytest.raises(BrowserAuthorityError):
        service.execute(plan, confirmation.confirmation_id)  # type: ignore[attr-defined]
    services.close()
    audit.close()
