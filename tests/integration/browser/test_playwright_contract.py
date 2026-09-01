import sys

import pytest
from playwright.sync_api import Route

from pc_manager_agent.browser.playwright_adapter import PlaywrightBrowserAdapter
from pc_manager_agent.domain.browser import BrowserActionKind, BrowserActionRequest
from pc_manager_agent.safety.browser.network import BrowserUrlPolicy


class _SyntheticResolver:
    def resolve(self, hostname: str) -> tuple[str, ...]:
        assert hostname == "synthetic.example"
        return ("93.184.216.34",)


def _fulfill(route: Route) -> bool:
    if route.request.url.endswith("/report.pdf"):
        route.fulfill(
            status=200,
            headers={
                "content-type": "application/pdf",
                "content-disposition": 'attachment; filename="report.pdf"',
            },
            body=b"%PDF-1.7\n%%EOF",
        )
        return True
    if route.request.resource_type == "document":
        if route.request.url.endswith("/next"):
            body = "<main><h1>Second page</h1><a href='/'>Previous page</a></main>"
        else:
            body = (
                "<main><h1>Safe page</h1>"
                "<input type='search' aria-label='Search'/>"
                "<button type='button'>Show details</button>"
                "<input type='checkbox' aria-label='Only reports'/>"
                "<select aria-label='Format'><option>PDF</option></select>"
                "<table aria-label='Results'><tr><td>One</td></tr></table>"
                "<a href='/next'>Next page</a>"
                "<a href='/report.pdf' download='report.pdf'>Download Report</a>"
                "</main>"
            )
        route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=(
                "<!doctype html><html><head><title>Synthetic</title></head>"
                f"<body>{body}</body></html>"
            ),
        )
    else:
        route.abort("blockedbyclient")
    return True


@pytest.mark.windows
@pytest.mark.playwright
@pytest.mark.skipif(sys.platform != "win32", reason="Stage 5C production target is Windows")
def test_real_playwright_uses_sandboxed_ephemeral_context_with_synthetic_transport() -> None:
    adapter = PlaywrightBrowserAdapter(
        url_policy=BrowserUrlPolicy(_SyntheticResolver()),
        route_fulfiller=_fulfill,
    )
    try:
        session = adapter.start(headless=True)
        observation = adapter.navigate("https://synthetic.example/")
        assert session.profile_persistent is False
        assert session.cookies_imported is False
        assert observation.title == "Synthetic"
        assert "Safe page" in observation.visible_text
        assert any(item.accessible_name == "Download Report" for item in observation.elements)

        def action(kind: BrowserActionKind, name: str | None = None, text: str | None = None):
            nonlocal observation
            element = (
                next(item for item in observation.elements if item.accessible_name == name)
                if name is not None
                else None
            )
            request = BrowserActionRequest(
                session_id=session.session_id,
                page_id=observation.page_id,
                navigation_id=observation.navigation_id,
                kind=kind,
                element=element,
                text=text,
            )
            result = adapter.perform(request)
            if result.observation is not None:
                observation = result.observation
            return result

        assert action(BrowserActionKind.OBSERVE).completed is True
        assert action(BrowserActionKind.SEARCH, "Search", "quarterly report").completed is True
        assert action(BrowserActionKind.EXPAND, "Show details").completed is True

        assert action(BrowserActionKind.OPEN_LINK, "Next page").completed is True
        assert "Second page" in observation.visible_text
        assert action(BrowserActionKind.BACK).completed is True
        assert "Safe page" in observation.visible_text
        assert action(BrowserActionKind.FORWARD).completed is True
        assert "Second page" in observation.visible_text
        assert action(BrowserActionKind.RELOAD).completed is True

        takeover = action(BrowserActionKind.BEGIN_USER_TAKEOVER)
        assert takeover.observation is None
        handback = adapter.perform(
            BrowserActionRequest(
                session_id=session.session_id,
                page_id=observation.page_id,
                navigation_id=observation.navigation_id,
                kind=BrowserActionKind.END_USER_TAKEOVER,
            )
        )
        assert handback.observation is not None
        assert handback.reason_code == "BROWSER_USER_TAKEOVER_ENDED_AND_REVALIDATED"
        with pytest.raises(RuntimeError, match="ALREADY_STARTED"):
            adapter.start(headless=True)
    finally:
        adapter.close()
        adapter.close()
