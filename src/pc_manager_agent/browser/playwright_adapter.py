"""Playwright implementation owned only by the disposable Browser Worker."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import UUID, uuid4

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    Route,
    sync_playwright,
)
from playwright.sync_api import (
    Error as PlaywrightError,
)

from pc_manager_agent.config.browser import BrowserSecuritySettings
from pc_manager_agent.domain.browser import (
    BrowserActionKind,
    BrowserActionRequest,
    BrowserActionResult,
    BrowserElementReference,
    BrowserElementRole,
    BrowserObservation,
    BrowserSessionDescriptor,
    BrowserSessionState,
)
from pc_manager_agent.domain.browser_downloads import BrowserWorkerDownload
from pc_manager_agent.safety.browser.content import BrowserPromptInjectionDetector
from pc_manager_agent.safety.browser.network import BrowserNetworkPolicyError, BrowserUrlPolicy


class PlaywrightBrowserAdapter:
    """Visible Chromium adapter with fixed semantic locators and no persistent profile."""

    def __init__(
        self,
        settings: BrowserSecuritySettings | None = None,
        url_policy: BrowserUrlPolicy | None = None,
        route_fulfiller: Callable[[Route], bool] | None = None,
    ) -> None:
        self._settings = settings or BrowserSecuritySettings()
        self._url_policy = url_policy or BrowserUrlPolicy()
        self._detector = BrowserPromptInjectionDetector()
        self._route_fulfiller = route_fulfiller
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._session: BrowserSessionDescriptor | None = None
        self._page_id = uuid4()
        self._navigation_id = uuid4()
        self._closed = False
        self._user_takeover = False
        self._confirmed_http_origins: set[str] = set()

    def start(self, *, headless: bool = False) -> BrowserSessionDescriptor:
        """Launch a sandboxed browser and an off-the-record context with service workers blocked."""
        if self._session is not None:
            raise RuntimeError("BROWSER_SESSION_ALREADY_STARTED")
        playwright = sync_playwright().start()
        try:
            browser = playwright.chromium.launch(
                headless=headless,
                chromium_sandbox=True,
                env=_sanitized_child_environment(),
                timeout=self._settings.navigation_timeout_seconds * 1_000,
            )
            context = browser.new_context(
                accept_downloads=True,
                ignore_https_errors=False,
                service_workers="block",
            )
            page = context.new_page()
            page.set_default_timeout(self._settings.action_timeout_seconds * 1_000)
            page.set_default_navigation_timeout(self._settings.navigation_timeout_seconds * 1_000)
            context.route("**/*", self._route_request)
            page.route_web_socket(
                "**/*",
                lambda websocket: websocket.close(code=1008, reason="Blocked by browser policy"),
            )
        except Exception:
            playwright.stop()
            raise
        self._playwright = playwright
        self._browser = browser
        self._context = context
        self._page = page
        self._session = BrowserSessionDescriptor(
            state=BrowserSessionState.ACTIVE,
            headless=headless,
        )
        return self._session

    def navigate(self, url: str, *, allow_http: bool = False) -> BrowserObservation:
        """Freshly validate and navigate to one public HTTPS URL."""
        page, _session = self._require_active()
        validated = self._url_policy.validate(url, allow_http=allow_http)
        if validated.insecure_http:
            self._confirmed_http_origins.add(validated.origin)
        page.goto(validated.normalized_url, wait_until="domcontentloaded")
        self._navigation_id = uuid4()
        self._url_policy.validate(page.url, allow_http=self._http_is_confirmed(page.url))
        return self.observe()

    def observe(self) -> BrowserObservation:
        """Read bounded visible text and fixed accessibility roles without page-provided code."""
        page, session = self._require_active()
        visible_text = ""
        body = page.locator("body")
        if body.count() == 1:
            visible_text = body.inner_text(timeout=self._settings.action_timeout_seconds * 1_000)
        truncated = len(visible_text) > self._settings.max_visible_characters
        visible_text = visible_text[: self._settings.max_visible_characters]
        elements: list[BrowserElementReference] = []
        role_limits = {
            BrowserElementRole.LINK: self._settings.max_links,
            BrowserElementRole.BUTTON: self._settings.max_controls,
            BrowserElementRole.SEARCHBOX: self._settings.max_controls,
            BrowserElementRole.TEXTBOX: self._settings.max_controls,
            BrowserElementRole.CHECKBOX: self._settings.max_controls,
            BrowserElementRole.RADIO: self._settings.max_controls,
            BrowserElementRole.COMBOBOX: self._settings.max_controls,
            BrowserElementRole.TABLE: self._settings.max_tables,
            BrowserElementRole.HEADING: self._settings.max_semantic_nodes,
            BrowserElementRole.ROW: self._settings.max_semantic_nodes,
            BrowserElementRole.CELL: self._settings.max_semantic_nodes,
        }
        for role, role_limit in role_limits.items():
            if len(elements) >= self._settings.max_semantic_nodes:
                truncated = True
                break
            locator = _role_locator(page, role)
            count = min(locator.count(), role_limit)
            for index in range(count):
                if len(elements) >= self._settings.max_semantic_nodes:
                    truncated = True
                    break
                item = locator.nth(index)
                if not item.is_visible():
                    continue
                name = _accessible_name(item)
                if not name:
                    continue
                raw_href = item.get_attribute("href") if role is BrowserElementRole.LINK else None
                href = urljoin(page.url, raw_href) if raw_href is not None else None
                elements.append(
                    BrowserElementReference.create(
                        session_id=session.session_id,
                        page_id=self._page_id,
                        navigation_id=self._navigation_id,
                        role=role,
                        accessible_name=name,
                        href=href,
                    )
                )
            if locator.count() > role_limit:
                truncated = True
        return BrowserObservation(
            session_id=session.session_id,
            page_id=self._page_id,
            navigation_id=self._navigation_id,
            url=page.url,
            title=page.title()[:1_000],
            visible_text=visible_text,
            elements=tuple(elements),
            prompt_injection_signals=self._detector.detect(visible_text),
            truncated=truncated,
        )

    def perform(self, action: BrowserActionRequest) -> BrowserActionResult:
        """Freshly re-resolve one semantic target; zero or multiple matches fail closed."""
        if action.kind is BrowserActionKind.END_USER_TAKEOVER:
            page, session = self._require_session()
            if not self._user_takeover:
                raise RuntimeError("BROWSER_USER_TAKEOVER_NOT_ACTIVE")
            self._user_takeover = False
            self._session = session.model_copy(update={"state": BrowserSessionState.ACTIVE})
            page.reload(wait_until="domcontentloaded")
            observation = self._observe_after_action()
            return BrowserActionResult(
                action_id=action.action_id,
                session_id=session.session_id,
                completed=True,
                reason_code="BROWSER_USER_TAKEOVER_ENDED_AND_REVALIDATED",
                observation=observation,
            )
        page, session = self._require_active()
        self._validate_generation(action, session.session_id)
        if action.kind is BrowserActionKind.BEGIN_USER_TAKEOVER:
            self._user_takeover = True
            self._session = session.model_copy(update={"state": BrowserSessionState.USER_TAKEOVER})
            return BrowserActionResult(
                action_id=action.action_id,
                session_id=session.session_id,
                completed=True,
                reason_code="BROWSER_USER_TAKEOVER_ACTIVE",
            )
        if action.kind is BrowserActionKind.OBSERVE:
            observation = self.observe()
        elif action.kind is BrowserActionKind.BACK:
            page.go_back(wait_until="domcontentloaded")
            observation = self._observe_after_action()
        elif action.kind is BrowserActionKind.FORWARD:
            page.go_forward(wait_until="domcontentloaded")
            observation = self._observe_after_action()
        elif action.kind is BrowserActionKind.RELOAD:
            page.reload(wait_until="domcontentloaded")
            observation = self._observe_after_action()
        else:
            if action.element is None:
                raise RuntimeError("BROWSER_ELEMENT_REQUIRED")
            locator = self._fresh_locator(action.element)
            if action.kind is BrowserActionKind.OPEN_LINK:
                raw_href = locator.get_attribute("href")
                href = urljoin(page.url, raw_href) if raw_href is not None else None
                if href is None or href != action.element.href:
                    raise RuntimeError("BROWSER_ELEMENT_HREF_CHANGED")
                self._url_policy.validate(href, allow_http=self._http_is_confirmed(href))
                locator.click()
                page.wait_for_load_state("domcontentloaded")
            elif action.kind in {BrowserActionKind.SEARCH, BrowserActionKind.FILTER}:
                if action.text is None:
                    raise RuntimeError("BROWSER_ACTION_TEXT_REQUIRED")
                locator.fill(action.text)
                locator.press("Enter")
                page.wait_for_load_state("domcontentloaded")
            elif action.kind in {
                BrowserActionKind.NEXT_PAGE,
                BrowserActionKind.PREVIOUS_PAGE,
                BrowserActionKind.EXPAND,
            }:
                locator.click()
            else:
                raise RuntimeError("BROWSER_ACTION_NOT_IMPLEMENTED")
            observation = self._observe_after_action()
        return BrowserActionResult(
            action_id=action.action_id,
            session_id=session.session_id,
            completed=True,
            reason_code="BROWSER_ACTION_VERIFIED",
            observation=observation,
        )

    def download(
        self, action: BrowserActionRequest, temporary_directory: Path
    ) -> BrowserWorkerDownload:
        """Download one freshly resolved link into an opaque Agent-owned temporary filename."""
        page, session = self._require_active()
        self._validate_generation(action, session.session_id)
        if action.kind is not BrowserActionKind.DOWNLOAD_DOCUMENT or action.element is None:
            raise RuntimeError("BROWSER_DOWNLOAD_ACTION_REQUIRED")
        locator = self._fresh_locator(action.element)
        raw_href = locator.get_attribute("href")
        href = urljoin(page.url, raw_href) if raw_href is not None else None
        if href is None or href != action.element.href:
            raise RuntimeError("BROWSER_DOWNLOAD_HREF_CHANGED")
        self._url_policy.validate(href, allow_http=self._http_is_confirmed(href))
        temporary_directory.mkdir(parents=True, exist_ok=True)
        temporary_path = temporary_directory / f"{uuid4()}.download"
        with page.expect_download(
            timeout=self._settings.navigation_timeout_seconds * 1_000
        ) as info:
            locator.click()
        download = info.value
        download.save_as(temporary_path)
        return BrowserWorkerDownload(
            session_id=session.session_id,
            action_id=action.action_id,
            source_url=href,
            suggested_filename=download.suggested_filename,
            temporary_path=temporary_path,
        )

    def cancel(self) -> None:
        """Close immediately; no action is replayed and no session is resumed."""
        if self._session is not None:
            self._session = self._session.model_copy(
                update={"state": BrowserSessionState.CANCELLED}
            )
        self.close()

    def close(self) -> None:
        """Destroy context, cookies, storage, browser, and Playwright driver."""
        if self._closed:
            return
        self._closed = True
        if self._context is not None:
            self._context.close()
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        if self._session is not None and self._session.state is not BrowserSessionState.CANCELLED:
            self._session = self._session.model_copy(update={"state": BrowserSessionState.CLOSED})

    def _route_request(self, route: Route) -> None:
        """Block non-read methods, sockets, and every freshly non-public destination."""
        request = route.request
        if not self._user_takeover and request.method.upper() not in {"GET", "HEAD"}:
            route.abort("blockedbyclient")
            return
        if request.resource_type in {"websocket", "eventsource"}:
            route.abort("blockedbyclient")
            return
        try:
            self._url_policy.validate(
                request.url,
                allow_http=self._http_is_confirmed(request.url),
            )
        except BrowserNetworkPolicyError:
            route.abort("blockedbyclient")
            return
        if self._route_fulfiller is not None and self._route_fulfiller(route):
            return
        route.continue_()

    def _fresh_locator(self, reference: BrowserElementReference) -> Locator:
        page, _session = self._require_active()
        locator = _role_locator(page, reference.role, name=reference.accessible_name)
        if locator.count() != 1 or not locator.is_visible():
            raise RuntimeError("BROWSER_ELEMENT_STALE_OR_AMBIGUOUS")
        return locator

    def _validate_generation(self, action: BrowserActionRequest, session_id: UUID) -> None:
        if action.session_id != session_id:
            raise RuntimeError("BROWSER_SESSION_MISMATCH")
        if action.page_id is not None and action.page_id != self._page_id:
            raise RuntimeError("BROWSER_PAGE_MISMATCH")
        if action.navigation_id is not None and action.navigation_id != self._navigation_id:
            raise RuntimeError("BROWSER_NAVIGATION_STALE")

    def _observe_after_action(self) -> BrowserObservation:
        page, _session = self._require_active()
        self._url_policy.validate(page.url, allow_http=self._http_is_confirmed(page.url))
        self._navigation_id = uuid4()
        return self.observe()

    def _http_is_confirmed(self, url: str) -> bool:
        try:
            parts = urlsplit(url)
            if parts.scheme.casefold() != "http" or parts.hostname is None:
                return False
            origin = f"http://{parts.hostname.casefold()}"
            if parts.port not in {None, 80}:
                origin = f"{origin}:{parts.port}"
        except ValueError:
            return False
        return origin in self._confirmed_http_origins

    def _require_active(self) -> tuple[Page, BrowserSessionDescriptor]:
        page, session = self._require_session()
        if session.state is not BrowserSessionState.ACTIVE:
            raise RuntimeError("BROWSER_SESSION_NOT_ACTIVE")
        return page, session

    def _require_session(self) -> tuple[Page, BrowserSessionDescriptor]:
        if self._closed or self._page is None or self._session is None:
            raise RuntimeError("BROWSER_SESSION_NOT_ACTIVE")
        return self._page, self._session


def _role_locator(page: Page, role: BrowserElementRole, name: str | None = None) -> Locator:
    if role is BrowserElementRole.LINK:
        return (
            page.get_by_role("link")
            if name is None
            else page.get_by_role("link", name=name, exact=True)
        )
    if role is BrowserElementRole.BUTTON:
        return (
            page.get_by_role("button")
            if name is None
            else page.get_by_role("button", name=name, exact=True)
        )
    if role is BrowserElementRole.HEADING:
        return (
            page.get_by_role("heading")
            if name is None
            else page.get_by_role("heading", name=name, exact=True)
        )
    if role is BrowserElementRole.TEXTBOX:
        return (
            page.get_by_role("textbox")
            if name is None
            else page.get_by_role("textbox", name=name, exact=True)
        )
    if role is BrowserElementRole.SEARCHBOX:
        return (
            page.get_by_role("searchbox")
            if name is None
            else page.get_by_role("searchbox", name=name, exact=True)
        )
    if role is BrowserElementRole.CHECKBOX:
        return (
            page.get_by_role("checkbox")
            if name is None
            else page.get_by_role("checkbox", name=name, exact=True)
        )
    if role is BrowserElementRole.RADIO:
        return (
            page.get_by_role("radio")
            if name is None
            else page.get_by_role("radio", name=name, exact=True)
        )
    if role is BrowserElementRole.COMBOBOX:
        return (
            page.get_by_role("combobox")
            if name is None
            else page.get_by_role("combobox", name=name, exact=True)
        )
    if role is BrowserElementRole.TABLE:
        return (
            page.get_by_role("table")
            if name is None
            else page.get_by_role("table", name=name, exact=True)
        )
    if role is BrowserElementRole.ROW:
        return (
            page.get_by_role("row")
            if name is None
            else page.get_by_role("row", name=name, exact=True)
        )
    return (
        page.get_by_role("cell")
        if name is None
        else page.get_by_role("cell", name=name, exact=True)
    )


def _accessible_name(locator: Locator) -> str:
    for attribute in ("aria-label", "title", "placeholder", "alt"):
        value = locator.get_attribute(attribute)
        if value and value.strip():
            return value.strip()[:500]
    try:
        value = locator.inner_text().strip()
    except PlaywrightError:
        return ""
    return " ".join(value.split())[:500]


def _sanitized_child_environment() -> dict[str, str | float | bool]:
    """Keep OS/runtime variables but remove credential-like values from Chromium."""
    blocked_markers = (
        "API_KEY",
        "PASSWORD",
        "SECRET",
        "TOKEN",
        "COOKIE",
        "OPENAI",
        "AUTHORIZATION",
        "CREDENTIAL",
    )
    return {
        name: value
        for name, value in os.environ.items()
        if not any(marker in name.upper() for marker in blocked_markers)
    }
