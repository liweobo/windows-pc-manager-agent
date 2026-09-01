"""Deterministic browser fake for unit, GUI, and integration tests."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

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


class FakeBrowserAdapter:
    """Never contacts a network and only exposes explicitly supplied synthetic pages."""

    def __init__(self, pages: dict[str, tuple[str, str]] | None = None) -> None:
        self._pages = pages or {"https://example.com/": ("Example", "Synthetic page")}
        self._session = BrowserSessionDescriptor(headless=True)
        self._page_id = uuid4()
        self._navigation_id = uuid4()
        self._current_url = "about:blank"
        self._cancelled = False
        self.actions: list[BrowserActionRequest] = []
        self.download_bytes: bytes = b"%PDF-1.7\n%%EOF"

    def start(self, *, headless: bool = False) -> BrowserSessionDescriptor:
        """Start an empty synthetic context."""
        self._session = BrowserSessionDescriptor(
            headless=headless, state=BrowserSessionState.ACTIVE
        )
        self._cancelled = False
        return self._session

    def navigate(self, url: str, *, allow_http: bool = False) -> BrowserObservation:
        """Select one registered synthetic page."""
        self._require_active()
        if url not in self._pages:
            raise RuntimeError("BROWSER_FAKE_URL_NOT_FOUND")
        self._current_url = url
        self._navigation_id = uuid4()
        return self.observe()

    def observe(self) -> BrowserObservation:
        """Return a bounded observation with one deterministic example link."""
        self._require_active()
        title, text = self._pages.get(self._current_url, ("Blank", ""))
        href = next((url for url in self._pages if url != self._current_url), self._current_url)
        element = BrowserElementReference.create(
            session_id=self._session.session_id,
            page_id=self._page_id,
            navigation_id=self._navigation_id,
            role=BrowserElementRole.LINK,
            accessible_name="Synthetic link",
            href=href,
        )
        return BrowserObservation(
            session_id=self._session.session_id,
            page_id=self._page_id,
            navigation_id=self._navigation_id,
            url=self._current_url,
            title=title,
            visible_text=text,
            elements=(element,),
        )

    def perform(self, action: BrowserActionRequest) -> BrowserActionResult:
        """Record an allowlisted action and simulate semantic navigation."""
        self._require_action_session(action.session_id)
        self.actions.append(action)
        observation: BrowserObservation | None = None
        if action.kind is BrowserActionKind.OPEN_LINK and action.element is not None:
            if action.element.href is None:
                raise RuntimeError("BROWSER_FAKE_LINK_HAS_NO_HREF")
            observation = self.navigate(action.element.href)
        elif action.kind in {
            BrowserActionKind.OBSERVE,
            BrowserActionKind.RELOAD,
            BrowserActionKind.EXPAND,
            BrowserActionKind.SEARCH,
            BrowserActionKind.FILTER,
            BrowserActionKind.NEXT_PAGE,
            BrowserActionKind.PREVIOUS_PAGE,
        }:
            observation = self.observe()
        return BrowserActionResult(
            action_id=action.action_id,
            session_id=action.session_id,
            completed=True,
            reason_code="BROWSER_FAKE_ACTION_COMPLETED",
            observation=observation,
        )

    def download(
        self, action: BrowserActionRequest, temporary_directory: Path
    ) -> BrowserWorkerDownload:
        """Create one Agent-owned synthetic PDF without using a network."""
        self._require_action_session(action.session_id)
        if action.kind is not BrowserActionKind.DOWNLOAD_DOCUMENT or action.element is None:
            raise RuntimeError("BROWSER_FAKE_DOWNLOAD_ACTION_REQUIRED")
        temporary_directory.mkdir(parents=True, exist_ok=True)
        path = temporary_directory / f"{action.action_id}.download"
        path.write_bytes(self.download_bytes)
        return BrowserWorkerDownload(
            session_id=action.session_id,
            action_id=action.action_id,
            source_url=action.element.href or self._current_url,
            suggested_filename="document.pdf",
            temporary_path=path,
        )

    def cancel(self) -> None:
        """Cancel and close the fake session."""
        self._cancelled = True
        self._session = self._session.model_copy(update={"state": BrowserSessionState.CANCELLED})

    def close(self) -> None:
        """Close without preserving state."""
        self._session = self._session.model_copy(update={"state": BrowserSessionState.CLOSED})

    def _require_active(self) -> None:
        if self._cancelled or self._session.state is not BrowserSessionState.ACTIVE:
            raise RuntimeError("BROWSER_SESSION_NOT_ACTIVE")

    def _require_action_session(self, session_id: UUID) -> None:
        self._require_active()
        if session_id != self._session.session_id:
            raise RuntimeError("BROWSER_SESSION_MISMATCH")
