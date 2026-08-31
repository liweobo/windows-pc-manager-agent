"""Evidence-linked, non-executing recommendations for Stage 4E1."""

from __future__ import annotations

from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.system_optimization import (
    CleanupCandidate,
    CleanupCategory,
    CleanupEvidenceOrigin,
    CleanupSafetyClassification,
    ExpectedBenefit,
    OptimizationConfidence,
    OptimizationGoal,
    OptimizationRecommendation,
    PerformanceCategory,
    PerformanceFinding,
    RecommendationType,
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
        if OptimizationGoal.FREE_DISK_SPACE in goals:
            recommendations.extend(self._storage_reviews(reviewable))
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
                    recommendation_type=RecommendationType.REVIEW_STARTUP_ITEM,
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
                    recommendation_type=RecommendationType.REVIEW_HIGH_RESOURCE_PROCESS,
                    goal=goal,
                    title="在问题发生时重复观察资源占用",
                    explanation="比较多次采样和正在运行的工作，再决定是否进行独立的受控操作。",
                    evidence_references=matched,
                    expected_benefit=ExpectedBenefit.MODERATE,
                    confidence=OptimizationConfidence.MEDIUM,
                    future_risk_level=RiskLevel.R0,
                )
            )
        software_findings = tuple(
            item.finding_id
            for item in findings
            if item.category is PerformanceCategory.POSSIBLE_SOFTWARE_BLOAT
        )
        if software_findings:
            recommendations.append(
                OptimizationRecommendation(
                    recommendation_type=RecommendationType.REVIEW_INSTALLED_SOFTWARE,
                    goal=OptimizationGoal.FREE_DISK_SPACE,
                    title="查看已安装软件及其体积估算",
                    explanation="软件较大并不表示应卸载；请在软件模块重新选择并检查当前身份。",
                    evidence_references=software_findings[:100],
                    expected_benefit=ExpectedBenefit.UNKNOWN,
                    confidence=OptimizationConfidence.LOW,
                    future_risk_level=RiskLevel.R0,
                    future_stage="Stage 4D 独立查看流程",
                )
            )
        if not recommendations and PerformanceCategory.DISK_SPACE_PRESSURE in categories:
            recommendations.append(
                OptimizationRecommendation(
                    recommendation_type=RecommendationType.FREE_DISK_SPACE,
                    goal=OptimizationGoal.FREE_DISK_SPACE,
                    title="查看空间压力，再选择需要分析的来源",
                    explanation="没有可直接处理的对象；这里只提供分类查看，不创建混合清理授权。",
                    evidence_references=tuple(
                        item.finding_id
                        for item in findings
                        if item.category is PerformanceCategory.DISK_SPACE_PRESSURE
                    )[:100],
                    expected_benefit=ExpectedBenefit.UNKNOWN,
                    confidence=OptimizationConfidence.MEDIUM,
                    future_risk_level=RiskLevel.R0,
                )
            )
        if not recommendations:
            evidence = tuple(item.finding_id for item in findings[:10])
            recommendations.append(
                OptimizationRecommendation(
                    recommendation_type=(
                        RecommendationType.NO_ACTION_NEEDED
                        if categories == {PerformanceCategory.NO_CLEAR_BOTTLENECK}
                        else RecommendationType.MANUAL_REVIEW
                    ),
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

    @staticmethod
    def _storage_reviews(
        candidates: tuple[CleanupCandidate, ...],
    ) -> tuple[OptimizationRecommendation, ...]:
        """Group explicit evidence by semantic review type, not by a filename or title."""
        groups: dict[tuple[RecommendationType, str], list[CleanupCandidate]] = {}
        for candidate in candidates:
            reference = candidate.source_reference
            if reference is not None and reference.origin is CleanupEvidenceOrigin.STAGE4D3_REPORT:
                kind = RecommendationType.REVIEW_SOFTWARE_RESIDUAL
            else:
                kind = {
                    CleanupCategory.USER_TEMP: RecommendationType.REVIEW_TEMP_STORAGE,
                    CleanupCategory.APPLICATION_CACHE: RecommendationType.REVIEW_APPLICATION_CACHE,
                    CleanupCategory.CRASH_DUMP: RecommendationType.REVIEW_CRASH_DUMPS,
                    CleanupCategory.RECYCLE_BIN_CONTENT: RecommendationType.REVIEW_RECYCLE_BIN,
                    CleanupCategory.LARGE_FILE: RecommendationType.REVIEW_LARGE_FILES,
                    CleanupCategory.INACTIVE_LARGE_FILE: RecommendationType.REVIEW_INACTIVE_FILES,
                    CleanupCategory.DUPLICATE_FILE: RecommendationType.REVIEW_DUPLICATES,
                }.get(candidate.category, RecommendationType.MANUAL_REVIEW)
            provenance = (
                str(reference.upstream_report_id)
                if reference is not None and kind is RecommendationType.REVIEW_SOFTWARE_RESIDUAL
                else ""
            )
            groups.setdefault((kind, provenance), []).append(candidate)
        labels = {
            RecommendationType.REVIEW_TEMP_STORAGE: "查看当前用户临时文件候选",
            RecommendationType.REVIEW_APPLICATION_CACHE: "查看已知缓存候选",
            RecommendationType.REVIEW_CRASH_DUMPS: "查看当前用户崩溃转储候选",
            RecommendationType.REVIEW_RECYCLE_BIN: "独立查看回收站内容与不可恢复风险",
            RecommendationType.REVIEW_LARGE_FILES: "在文件分析中查看大文件",
            RecommendationType.REVIEW_INACTIVE_FILES: "复核疑似闲置文件及判断依据",
            RecommendationType.REVIEW_DUPLICATES: "查看重复文件，不自动选择删除对象",
            RecommendationType.REVIEW_SOFTWARE_RESIDUAL: "在软件残留模块重新复核",
            RecommendationType.MANUAL_REVIEW: "人工复核其他空间观察",
        }
        return tuple(
            OptimizationRecommendation(
                recommendation_type=kind,
                goal=OptimizationGoal.FREE_DISK_SPACE,
                title=labels[kind],
                explanation="建议仅用于进入独立复核。当前身份、安全、用户选择和确认决定后续操作。",
                evidence_references=tuple(item.candidate_id for item in items[:100]),
                expected_benefit=ExpectedBenefit.UNKNOWN,
                confidence=OptimizationConfidence.MEDIUM,
                future_risk_level=RiskLevel.R0,
            )
            for (kind, _provenance), items in groups.items()
        )
