"""Isolated browser adapters and finite worker protocol."""

from pc_manager_agent.browser.adapter import BrowserAdapter
from pc_manager_agent.browser.client import BrowserWorkerClient
from pc_manager_agent.browser.fake import FakeBrowserAdapter

__all__ = ["BrowserAdapter", "BrowserWorkerClient", "FakeBrowserAdapter"]
