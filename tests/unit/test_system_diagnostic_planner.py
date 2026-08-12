from __future__ import annotations

import pytest

from pc_manager_agent.domain.system_diagnostics import (
    DiagnosticIntent,
    DiagnosticIntentDraft,
    SystemCollector,
)
from pc_manager_agent.orchestration.system_diagnostic_planner import (
    DiagnosticPlanCompiler,
    classify_diagnostic_intent,
    extract_software_search_term,
    is_diagnostic_request,
)
from pc_manager_agent.safety.system_diagnostics import DiagnosticSafetyValidator
from tests.fixtures.system_diagnostics import build_registry


@pytest.mark.parametrize(
    ("goal", "intent"),
    (
        ("电脑为什么卡顿", DiagnosticIntent.PERFORMANCE),
        ("查看内存", DiagnosticIntent.MEMORY),
        ("list installed software", DiagnosticIntent.SOFTWARE),
        ("查看启动项", DiagnosticIntent.STARTUP),
        ("为什么电脑这么卡", DiagnosticIntent.PERFORMANCE),
        ("C盘是不是快满了", DiagnosticIntent.DISKS),
        ("电脑上安装了哪些 Adobe 软件", DiagnosticIntent.SOFTWARE),
        ("ordinary text", DiagnosticIntent.OVERVIEW),
    ),
)
def test_local_intent_classifier(goal: str, intent: DiagnosticIntent) -> None:
    assert classify_diagnostic_intent(goal) is intent


def test_chat_diagnostic_detection_is_explicit() -> None:
    assert is_diagnostic_request("诊断电脑性能")
    assert is_diagnostic_request("为什么电脑这么卡")
    assert is_diagnostic_request("这个 Windows 服务现在是什么状态")
    assert is_diagnostic_request("电脑上安装了哪些 Python")
    assert not is_diagnostic_request("帮我整理文件")


def test_software_search_term_is_extracted_without_model_guessing() -> None:
    assert extract_software_search_term("电脑上安装了哪些 Adobe 软件？") == "Adobe"
    assert extract_software_search_term("查看已安装软件清单") is None


def test_process_termination_wording_compiles_to_read_only_inspection() -> None:
    registry = build_registry()
    compiler = DiagnosticPlanCompiler(registry)
    plan = compiler.compile(
        "把最占 CPU 的程序关掉",
        compiler.local_draft("把最占 CPU 的程序关掉"),
    )
    assert plan.collectors == (
        SystemCollector.SYSTEM_INFO,
        SystemCollector.CPU,
        SystemCollector.PROCESSES,
    )
    assert all(registry.manifest(item.value).read_only for item in plan.collectors)


def test_hallucinated_system_tool_is_not_a_valid_collector() -> None:
    with pytest.raises(ValueError):
        DiagnosticIntentDraft(
            intent=DiagnosticIntent.CPU,
            requested_collectors=("system.kill",),
        )


def test_compiler_adds_system_info_and_rejects_scope_expansion() -> None:
    registry = build_registry()
    compiler = DiagnosticPlanCompiler(registry)
    plan = compiler.compile(
        "memory",
        DiagnosticIntentDraft(
            intent=DiagnosticIntent.MEMORY,
            requested_collectors=(SystemCollector.MEMORY,),
        ),
    )
    assert plan.collectors == (SystemCollector.SYSTEM_INFO, SystemCollector.MEMORY)
    assert DiagnosticSafetyValidator(registry).review(plan).approved
    with pytest.raises(ValueError, match="exceed"):
        compiler.compile(
            "memory",
            DiagnosticIntentDraft(
                intent=DiagnosticIntent.MEMORY,
                requested_collectors=(SystemCollector.SERVICES,),
            ),
        )


def test_all_registered_system_tools_are_r0_read_only() -> None:
    registry = build_registry()
    assert registry.names == tuple(sorted(item.value for item in SystemCollector))
    for name in registry.names:
        manifest = registry.manifest(name)
        assert manifest.risk_level.value == "R0"
        assert manifest.read_only
        assert manifest.requires_confirmation
        assert not manifest.requires_runtime_confirmation
        assert manifest.rollback_level.value == "NONE"
