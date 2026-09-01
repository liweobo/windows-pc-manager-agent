"""Provider-neutral browser adapter contract."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pc_manager_agent.domain.browser import (
    BrowserActionRequest,
    BrowserActionResult,
    BrowserObservation,
    BrowserSessionDescriptor,
)
from pc_manager_agent.domain.browser_downloads import BrowserWorkerDownload


class BrowserAdapter(Protocol):
    """Finite adapter contract with no generic script, selector, or request API."""

    def start(self, *, headless: bool = False) -> BrowserSessionDescriptor:
        """Start one empty ephemeral context without importing user profile state."""

    def navigate(self, url: str, *, allow_http: bool = False) -> BrowserObservation:
        """Navigate to one prevalidated explicit URL and return a bounded observation."""

    def observe(self) -> BrowserObservation:
        """Return visible/accessibility-derived page data only."""

    def perform(self, action: BrowserActionRequest) -> BrowserActionResult:
        """Perform one finite semantic action after fresh identity checks."""

    def download(
        self, action: BrowserActionRequest, temporary_directory: Path
    ) -> BrowserWorkerDownload:
        """Download one semantic link into an Agent-owned temporary directory."""

    def cancel(self) -> None:
        """Stop future work and close the disposable context."""

    def close(self) -> None:
        """Close the context and browser without preserving storage state."""
