"""Finite intent classification and deterministic Stage 3 plan compilation."""

from __future__ import annotations

from collections.abc import Mapping

from pc_manager_agent.domain.system_diagnostics import (
    DiagnosticIntent,
    DiagnosticIntentDraft,
    DiagnosticPlan,
    SystemCollector,
)
from pc_manager_agent.tools.registry import ToolRegistry

_INTENT_COLLECTORS: Mapping[DiagnosticIntent, tuple[SystemCollector, ...]] = {
    DiagnosticIntent.OVERVIEW: tuple(SystemCollector),
    DiagnosticIntent.PERFORMANCE: (
        SystemCollector.SYSTEM_INFO,
        SystemCollector.CPU,
        SystemCollector.MEMORY,
        SystemCollector.DISKS,
        SystemCollector.PROCESSES,
    ),
    DiagnosticIntent.CPU: (
        SystemCollector.SYSTEM_INFO,
        SystemCollector.CPU,
        SystemCollector.PROCESSES,
    ),
    DiagnosticIntent.MEMORY: (
        SystemCollector.SYSTEM_INFO,
        SystemCollector.MEMORY,
        SystemCollector.PROCESSES,
    ),
    DiagnosticIntent.DISKS: (SystemCollector.SYSTEM_INFO, SystemCollector.DISKS),
    DiagnosticIntent.PROCESSES: (SystemCollector.SYSTEM_INFO, SystemCollector.PROCESSES),
    DiagnosticIntent.STARTUP: (SystemCollector.SYSTEM_INFO, SystemCollector.STARTUP),
    DiagnosticIntent.SERVICES: (SystemCollector.SYSTEM_INFO, SystemCollector.SERVICES),
    DiagnosticIntent.SOFTWARE: (SystemCollector.SYSTEM_INFO, SystemCollector.SOFTWARE),
}


def classify_diagnostic_intent(user_goal: str) -> DiagnosticIntent:
    """Classify common Chinese and English diagnostic requests without an LLM call."""
    normalized = user_goal.casefold()
    keyword_groups: tuple[tuple[DiagnosticIntent, tuple[str, ...]], ...] = (
        (DiagnosticIntent.STARTUP, ("启动项", "开机启动", "startup")),
        (DiagnosticIntent.SERVICES, ("服务", "services", "service list")),
        (
            DiagnosticIntent.SOFTWARE,
            ("已安装软件", "软件清单", "安装了哪些", "电脑上安装", "installed software"),
        ),
        (DiagnosticIntent.PROCESSES, ("进程", "process", "程序占用")),
        (DiagnosticIntent.DISKS, ("磁盘", "硬盘", "c盘", "c 盘", "disk", "storage")),
        (DiagnosticIntent.MEMORY, ("内存", "memory", "ram")),
        (DiagnosticIntent.CPU, ("cpu", "处理器")),
        (
            DiagnosticIntent.PERFORMANCE,
            ("性能", "卡顿", "很卡", "这么卡", "缓慢", "performance", "slow"),
        ),
    )
    for intent, keywords in keyword_groups:
        if any(keyword in normalized for keyword in keywords):
            return intent
    return DiagnosticIntent.OVERVIEW


def is_diagnostic_request(user_goal: str) -> bool:
    """Return whether chat text contains an explicit Stage 3 diagnostic concept."""
    normalized = user_goal.casefold()
    keywords = (
        "系统状态",
        "电脑状态",
        "诊断",
        "性能",
        "卡顿",
        "很卡",
        "这么卡",
        "缓慢",
        "cpu",
        "处理器",
        "内存",
        "磁盘",
        "硬盘",
        "c盘",
        "c 盘",
        "进程",
        "启动项",
        "开机启动",
        "服务列表",
        "windows 服务",
        "服务状态",
        "已安装软件",
        "软件清单",
        "安装了哪些",
        "电脑上安装",
        "performance",
        "process",
        "startup",
        "installed software",
    )
    return any(keyword in normalized for keyword in keywords)


def extract_software_search_term(user_goal: str) -> str | None:
    """Extract an optional product/publisher term from a software-inventory question."""
    value = user_goal.strip()
    removable = (
        "帮我",
        "请",
        "查看",
        "查找",
        "找一下",
        "电脑上",
        "电脑",
        "安装了",
        "安装的",
        "已安装",
        "有哪些",
        "哪些",
        "软件清单",
        "软件",
        "程序",
        "list",
        "show",
        "find",
        "installed",
        "software",
        "applications",
        "apps",
        "on my pc",
    )
    for item in removable:
        value = value.replace(item, "").replace(item.title(), "")
    normalized = " ".join(value.split()).strip("：:，,。?？ ")
    return normalized if 1 <= len(normalized) <= 100 else None


class DiagnosticPlanCompiler:
    """Compile a finite intent into an allow-listed, immutable R0 plan."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        sample_count: int = 3,
        sample_interval_seconds: float = 1.5,
        max_processes: int = 500,
        max_items: int = 5_000,
    ) -> None:
        self._registry = registry
        self._sample_count = sample_count
        self._sample_interval_seconds = sample_interval_seconds
        self._max_processes = max_processes
        self._max_items = max_items

    def local_draft(self, user_goal: str) -> DiagnosticIntentDraft:
        """Build a deterministic intent draft when no provider is configured."""
        intent = classify_diagnostic_intent(user_goal)
        return DiagnosticIntentDraft(
            intent=intent,
            requested_collectors=_INTENT_COLLECTORS[intent],
        )

    def compile(self, user_goal: str, draft: DiagnosticIntentDraft) -> DiagnosticPlan:
        """Intersect an untrusted draft with local policy and registered R0 manifests."""
        allowed = _INTENT_COLLECTORS[draft.intent]
        requested = tuple(dict.fromkeys(draft.requested_collectors))
        if not requested or any(item not in allowed for item in requested):
            raise ValueError("Requested collectors exceed the selected diagnostic intent")
        collectors = (
            (SystemCollector.SYSTEM_INFO, *requested)
            if SystemCollector.SYSTEM_INFO not in requested
            else requested
        )
        for collector in collectors:
            manifest = self._registry.manifest(collector.value)
            if not manifest.read_only or manifest.risk_level.value != "R0":
                raise ValueError(f"Diagnostic tool is not read-only R0: {collector.value}")
        return DiagnosticPlan(
            summary=f"Read-only Windows diagnostic: {draft.intent.value}",
            user_goal=user_goal,
            intent=draft.intent,
            collectors=collectors,
            sample_count=self._sample_count,
            sample_interval_seconds=self._sample_interval_seconds,
            max_processes=self._max_processes,
            max_items_per_collector=self._max_items,
        )
