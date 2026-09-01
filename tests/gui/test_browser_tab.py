from pathlib import Path

from PySide6.QtWidgets import QLabel

from pc_manager_agent.app.browser import BrowserServices
from pc_manager_agent.audit.browser import BrowserAuditLogger
from pc_manager_agent.audit.repository import AuditRepository
from pc_manager_agent.browser.fake import FakeBrowserAdapter
from pc_manager_agent.confirmation.browser import BrowserConfirmationService
from pc_manager_agent.orchestration.browser import BrowserPlanCompiler, BrowserTaskService
from pc_manager_agent.persistence.browser import (
    BrowserConfirmationRepository,
    BrowserWriteExecutionGuard,
)
from pc_manager_agent.safety.browser.actions import BrowserActionPolicy
from pc_manager_agent.safety.browser.network import BrowserUrlPolicy
from pc_manager_agent.tools.browser_tools import build_browser_registry
from pc_manager_agent.ui.browser_tab import BrowserTab


class _Resolver:
    def resolve(self, hostname: str) -> tuple[str, ...]:
        return ("93.184.216.34",)


def _services(tmp_path: Path) -> tuple[BrowserServices, AuditRepository]:
    adapter = FakeBrowserAdapter()
    repository = BrowserConfirmationRepository(tmp_path / "state.db")
    repository.initialize()
    audit = AuditRepository(tmp_path / "state.db")
    audit.initialize()
    guard = BrowserWriteExecutionGuard()
    policy = BrowserUrlPolicy(_Resolver())
    registry = build_browser_registry(adapter, write_guard=guard)
    service = BrowserTaskService(
        registry=registry,
        adapter=adapter,
        compiler=BrowserPlanCompiler(policy, BrowserActionPolicy()),
        confirmation=BrowserConfirmationService(repository, 60),
        confirmation_repository=repository,
        write_guard=guard,
        audit=BrowserAuditLogger(audit),
        temporary_directory=tmp_path / "temporary",
        url_policy=policy,
    )
    return (
        BrowserServices(
            registry=registry,
            service=service,
            confirmation_repository=repository,
        ),
        audit,
    )


def test_browser_tab_explains_isolation_and_blocked_remote_writes(
    qtbot: object, tmp_path: Path
) -> None:
    services, audit = _services(tmp_path)
    tab = BrowserTab(services)
    labels = " ".join(widget.text() for widget in tab.findChildren(QLabel))
    assert "不读取现有浏览器资料" in labels
    assert "购买" in labels
    assert "远程写入始终被阻止" in labels
    services.close()
    audit.close()
