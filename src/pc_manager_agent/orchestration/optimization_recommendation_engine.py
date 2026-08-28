"""Evidence-linked, non-executing recommendations for Stage 4E1."""

from __future__ import annotations

from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.system_optimization import (
    CleanupCandidate,
    CleanupSafetyClassification,
    ExpectedBenefit,
    OptimizationConfidence,
    OptimizationGoal,
    OptimizationRecommendation,
    PerformanceCategory,
    PerformanceFinding,
)


class OptimizationRecommendationEngine:
    """Translate structured evidence into cautious review suggestions only."""

    def build(
        self,
        goals: tuple[OptimizationGoal, ...],
        candidates: tuple[CleanupCandidate, ...],
        findings: tuple[PerformanceFinding, ...],
    ) -> tuple[OptimizationRecommendation, ...]:
        """Return deterministic advice; no output contains a command or tool argument."""
        recommendations: list[OptimizationRecommendation] = []
        reviewable = tuple(
            item
            for item in candidates
            if item.safety_classification
            in {
                CleanupSafetyClassification.LOW_RISK_CANDIDATE,
                CleanupSafetyClassification.CAUTION,
                CleanupSafetyClassification.HIGH_IMPACT,
            }
            and item.observed_size_bytes > 0
        )
        if OptimizationGoal.FREE_DISK_SPACE in goals and reviewable:
            recommendations.append(
                OptimizationRecommendation(
                    goal=OptimizationGoal.FREE_DISK_SPACE,
                    title="先查看空间候选及其保护原因",
                    explanation=(
                        "报告中的空间值是观察或保守估计；Stage 4E1 不会选择或清理任何对象。"
                    ),
                    evidence_references=tuple(item.candidate_id for item in reviewable[:100]),
                    expected_benefit=ExpectedBenefit.POTENTIALLY_HIGH,
                    confidence=OptimizationConfidence.MEDIUM,
                    future_risk_level=RiskLevel.R2,
                    future_stage="Stage 4E2（尚未实现）",
                )
            )
        categories = {item.category for item in findings}
        if (
            OptimizationGoal.IMPROVE_BOOT_TIME in goals
            and PerformanceCategory.STARTUP_LOAD in categories
        ):
            matched = tuple(
                item.finding_id
                for item in findings
                if item.category is PerformanceCategory.STARTUP_LOAD
            )
            recommendations.append(
                OptimizationRecommendation(
                    goal=OptimizationGoal.IMPROVE_BOOT_TIME,
                    title="复核普通第三方启动项",
                    explanation="先确认发布者和实际启动影响；不要仅凭条目数量禁用项目。",
                    evidence_references=matched,
                    expected_benefit=ExpectedBenefit.MODERATE,
                    confidence=OptimizationConfidence.LOW,
                    future_risk_level=RiskLevel.R2,
                    future_stage="现有 Stage 4B 独立流程",
                )
            )
        resource_categories = {
            PerformanceCategory.CPU_PRESSURE,
            PerformanceCategory.MEMORY_PRESSURE,
            PerformanceCategory.BACKGROUND_PROCESS_LOAD,
        }
        if categories & resource_categories:
            goal = (
                OptimizationGoal.REDUCE_BACKGROUND_LOAD
                if OptimizationGoal.REDUCE_BACKGROUND_LOAD in goals
                else OptimizationGoal.DIAGNOSE_SLOW_PC
            )
            matched = tuple(
                item.finding_id for item in findings if item.category in resource_categories
            )
            recommendations.append(
                OptimizationRecommendation(
                    goal=goal,
                    title="在问题发生时重复观察资源占用",
                    explanation="比较多次采样和正在运行的工作，再决定是否进行独立的受控操作。",
                    evidence_references=matched,
                    expected_benefit=ExpectedBenefit.MODERATE,
                    confidence=OptimizationConfidence.MEDIUM,
                    future_risk_level=RiskLevel.R0,
                )
            )
        if not recommendations:
            evidence = tuple(item.finding_id for item in findings[:10])
            recommendations.append(
                OptimizationRecommendation(
                    goal=goals[0],
                    title="保留当前报告并在症状出现时重新分析",
                    explanation="当前证据不足以支持清理或配置修改建议。",
                    evidence_references=evidence,
                    expected_benefit=ExpectedBenefit.LOW,
                    confidence=OptimizationConfidence.MEDIUM,
                    future_risk_level=RiskLevel.R0,
                )
            )
        return tuple(recommendations)
