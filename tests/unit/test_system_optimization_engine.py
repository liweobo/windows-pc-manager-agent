from __future__ import annotations

from pc_manager_agent.domain.system_optimization import (
    OptimizationGoal,
    PerformanceCategory,
)
from pc_manager_agent.orchestration.optimization_recommendation_engine import (
    OptimizationRecommendationEngine,
)
from pc_manager_agent.orchestration.performance_diagnostic_engine import (
    PerformanceDiagnosticEngine,
)
from tests.fixtures.system_optimization import build_system_snapshot


def test_performance_engine_requires_multiple_factors() -> None:
    findings = PerformanceDiagnosticEngine().analyze(build_system_snapshot())
    categories = {item.category for item in findings}
    assert PerformanceCategory.CPU_PRESSURE in categories
    assert PerformanceCategory.MEMORY_PRESSURE in categories
    assert PerformanceCategory.DISK_SPACE_PRESSURE in categories
    assert all(item.stage4e1_executable is False for item in findings)


def test_recommendations_are_non_executable_and_evidence_linked() -> None:
    findings = PerformanceDiagnosticEngine().analyze(build_system_snapshot())
    recommendations = OptimizationRecommendationEngine().build(
        (OptimizationGoal.DIAGNOSE_SLOW_PC,), (), findings
    )
    assert recommendations
    assert recommendations[0].evidence_references
    assert all(item.executable_in_current_stage is False for item in recommendations)
