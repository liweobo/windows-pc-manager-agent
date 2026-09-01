"""Synthetic Stage 5C model workload; it never starts a browser or contacts a network."""

from __future__ import annotations

import time
import tracemalloc
from uuid import uuid4

import pytest

from pc_manager_agent.domain.browser import (
    BrowserElementReference,
    BrowserElementRole,
    BrowserObservation,
)
from pc_manager_agent.safety.browser.content import BrowserPromptInjectionDetector


@pytest.mark.performance
def test_maximum_semantic_observation_remains_bounded() -> None:
    session_id = uuid4()
    page_id = uuid4()
    navigation_id = uuid4()
    tracemalloc.start()
    started = time.perf_counter()
    try:
        elements = tuple(
            BrowserElementReference.create(
                session_id=session_id,
                page_id=page_id,
                navigation_id=navigation_id,
                role=BrowserElementRole.LINK,
                accessible_name=f"Synthetic link {index}",
                href=f"https://example.com/document/{index}",
            )
            for index in range(5_000)
        )
        observation = BrowserObservation(
            session_id=session_id,
            page_id=page_id,
            navigation_id=navigation_id,
            url="https://example.com/",
            visible_text="A" * 40_000,
            elements=elements,
            prompt_injection_signals=BrowserPromptInjectionDetector().detect("A" * 40_000),
        )
        duration = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    print(f"browser observation: {duration:.3f}s; traced peak={peak / 1024**2:.2f}MiB")
    assert len(observation.elements) == 5_000
    assert len(observation.visible_text) == 40_000
    assert duration < 15.0
    assert peak < 256 * 1024**2
