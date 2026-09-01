"""Security budgets for the isolated Stage 5C browser runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BrowserSecuritySettings:
    """Finite browser limits; callers cannot relax policy through model output."""

    max_redirects: int = 5
    max_semantic_nodes: int = 5_000
    max_visible_characters: int = 40_000
    max_model_characters: int = 12_000
    max_links: int = 200
    max_controls: int = 100
    max_tables: int = 10
    max_download_bytes: int = 50 * 1024 * 1024
    action_timeout_seconds: float = 30.0
    navigation_timeout_seconds: float = 45.0
    confirmation_ttl_seconds: int = 120

    def __post_init__(self) -> None:
        """Reject unsafe or nonsensical configuration before a browser can start."""
        positive = (
            self.max_redirects,
            self.max_semantic_nodes,
            self.max_visible_characters,
            self.max_model_characters,
            self.max_links,
            self.max_controls,
            self.max_tables,
            self.max_download_bytes,
            self.action_timeout_seconds,
            self.navigation_timeout_seconds,
            self.confirmation_ttl_seconds,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("Browser security budgets must be positive")
        if self.max_model_characters > self.max_visible_characters:
            raise ValueError("Model content budget cannot exceed the local observation budget")
