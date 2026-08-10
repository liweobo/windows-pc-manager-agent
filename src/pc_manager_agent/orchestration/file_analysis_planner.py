"""Provider intent boundary and deterministic Stage 1 plan compilation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
from uuid import UUID, uuid4

from pc_manager_agent.authorization.service import AuthorizedPathService
from pc_manager_agent.confirmation.external_data import (
    ExternalDataConsentRequest,
    ExternalDataConsentService,
    ExternalDataPurpose,
)
from pc_manager_agent.domain.file_analysis import (
    AnalysisType,
    FileAnalysisIntentDraft,
    FileAnalysisPlan,
)
from pc_manager_agent.domain.plans import EstimatedImpact, PlanStep, TaskPlan, TaskScope
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.providers.llm.base import (
    AuthorizedRootOption,
    FileAnalysisPlannerRequest,
    LLMProvider,
)
from pc_manager_agent.safety.path_policy import path_is_within
from pc_manager_agent.tools.registry import ToolRegistry, UnknownToolError


@dataclass(frozen=True, slots=True)
class FileAnalysisPlanningResult:
    """Compiled plan and provider tracing data returned to orchestration/UI."""

    plan: FileAnalysisPlan
    provider: str
    provider_request_id: str | None


class FileAnalysisPlanCompiler:
    """Convert an untrusted intent into an allow-listed immutable tool plan."""

    _ANALYSIS_TO_TOOL: ClassVar[dict[AnalysisType, str]] = {
        AnalysisType.LARGE_FILES: "file.analyze.large",
        AnalysisType.INACTIVE_FILES: "file.analyze.inactive",
        AnalysisType.DUPLICATES: "file.analyze.duplicates",
    }

    def __init__(
        self,
        authorization: AuthorizedPathService,
        registry: ToolRegistry,
        *,
        max_files: int,
        timeout_seconds: float,
        batch_size: int = 250,
    ) -> None:
        self._authorization = authorization
        self._registry = registry
        self._max_files = max_files
        self._timeout_seconds = timeout_seconds
        self._batch_size = batch_size

    def compile(self, user_goal: str, draft: FileAnalysisIntentDraft) -> FileAnalysisPlan:
        """Resolve root IDs locally and derive every executable argument."""
        records = self._authorization.resolve_authorized(draft.authorized_root_ids)
        roots = tuple(record.path for record in records)
        self._reject_overlapping_roots(roots)
        policy = self._authorization.build_policy(draft.authorized_root_ids)
        session_id = uuid4()
        root_count = len(roots)
        per_root_limit = max(1, self._max_files // root_count)
        per_root_timeout = max(1.0, self._timeout_seconds / root_count)
        steps: list[PlanStep] = []
        all_exclusions: set[Path] = set()

        for index, root in enumerate(roots, start=1):
            exclusions = tuple(
                forbidden for forbidden in policy.forbidden_roots if path_is_within(forbidden, root)
            )
            all_exclusions.update(exclusions)
            steps.append(
                PlanStep(
                    step_id=f"step-scan-{index}",
                    tool_name="file.scan",
                    description=f"只读取授权目录 {root} 中的文件元数据",
                    arguments={
                        "root": str(root),
                        "excluded_paths": [str(path) for path in exclusions],
                        "max_files": per_root_limit,
                        "timeout_seconds": per_root_timeout,
                        "session_id": str(session_id),
                        "batch_size": self._batch_size,
                        "retain_files": False,
                    },
                    risk_level=RiskLevel.R0,
                    requires_confirmation=False,
                    rollback_level=RollbackLevel.NONE,
                    preconditions=("目录已由用户授权", "路径安全策略通过", "审计数据库可用"),
                    expected_postconditions=("未修改任何用户文件或目录",),
                )
            )

        for analysis in draft.analyses:
            tool_name = self._ANALYSIS_TO_TOOL[analysis]
            try:
                self._registry.manifest(tool_name)
            except UnknownToolError as exc:
                raise ValueError(f"Required analyzer is not registered: {tool_name}") from exc
            arguments: dict[str, object] = {"analysis_session_id": str(session_id)}
            if analysis is AnalysisType.LARGE_FILES:
                arguments["minimum_size_bytes"] = draft.filters.minimum_size_bytes
            elif analysis is AnalysisType.INACTIVE_FILES:
                arguments["inactive_days"] = draft.filters.inactive_days
            else:
                arguments.update({"quick_hash_bytes": 65_536, "byte_verify": True})
            steps.append(
                PlanStep(
                    step_id=f"step-{analysis.value.replace('_', '-')}",
                    tool_name=tool_name,
                    description=self._analysis_description(analysis),
                    arguments=arguments,
                    risk_level=RiskLevel.R0,
                    requires_confirmation=False,
                    rollback_level=RollbackLevel.NONE,
                    preconditions=("只读扫描会话已建立",),
                    expected_postconditions=("只产生结构化分析结论",),
                )
            )

        task_plan = TaskPlan(
            summary="对已授权目录执行只读文件分析",
            user_goal=user_goal,
            assumptions=(
                "访问时间可能被 Windows 延迟或禁用，只能作为疑似闲置证据",
                "重复文件只有经过内容哈希验证后才会分组",
                "任何扫描或分析步骤都不会移动、重命名或删除文件",
                f"匹配模式：{draft.match_mode.value}",
            ),
            scope=TaskScope(
                included_paths=roots,
                excluded_paths=tuple(sorted(all_exclusions, key=str)),
            ),
            steps=tuple(steps),
            estimated_impact=EstimatedImpact(
                files_read=None,
                files_modified=0,
                files_deleted=0,
            ),
            requires_plan_confirmation=True,
            requires_runtime_confirmation=False,
        )
        return FileAnalysisPlan(
            analysis_session_id=session_id,
            authorized_root_ids=draft.authorized_root_ids,
            filters=draft.filters,
            analyses=draft.analyses,
            match_mode=draft.match_mode,
            task_plan=task_plan,
        )

    @staticmethod
    def _reject_overlapping_roots(roots: tuple[Path, ...]) -> None:
        for index, root in enumerate(roots):
            for other in roots[index + 1 :]:
                if path_is_within(root, other) or path_is_within(other, root):
                    raise ValueError("One plan cannot contain overlapping authorized roots")

    @staticmethod
    def _analysis_description(analysis: AnalysisType) -> str:
        descriptions = {
            AnalysisType.LARGE_FILES: "按已确认的大小阈值分析大文件",
            AnalysisType.INACTIVE_FILES: "根据时间证据分析疑似长期未使用文件",
            AnalysisType.DUPLICATES: "分阶段读取候选内容并验证重复文件",
        }
        return descriptions[analysis]


class FileAnalysisPlanner:
    """Ask a provider for intent, then compile it without trusting provider authority."""

    def __init__(
        self,
        provider: LLMProvider,
        authorization: AuthorizedPathService,
        registry: ToolRegistry,
        compiler: FileAnalysisPlanCompiler,
        external_consent: ExternalDataConsentService,
    ) -> None:
        self._provider = provider
        self._authorization = authorization
        self._registry = registry
        self._compiler = compiler
        self._external_consent = external_consent

    def build_provider_request(
        self,
        user_goal: str,
        root_ids: tuple[UUID, ...] | None = None,
    ) -> FileAnalysisPlannerRequest:
        """Build a minimal request with root IDs and labels, never actual paths."""
        selected = (
            self._authorization.resolve_authorized(root_ids)
            if root_ids is not None
            else self._authorization.list_authorized()
        )
        options = tuple(
            AuthorizedRootOption(root_id=str(record.path_id), label=record.label)
            for record in selected
        )
        if not options:
            raise ValueError("Add an authorized directory before asking the model to plan")
        return FileAnalysisPlannerRequest(
            user_goal=user_goal,
            authorized_roots=options,
            allowed_analyses=tuple(AnalysisType),
            allowed_tools=self._registry.names,
        )

    def request_external_consent(
        self,
        user_goal: str,
        root_ids: tuple[UUID, ...] | None = None,
    ) -> ExternalDataConsentRequest:
        """Request approval for the exact root-ID-only planner payload."""
        request = self.build_provider_request(user_goal, root_ids)
        payload = request.model_dump(mode="json")
        return self._external_consent.request(
            purpose=ExternalDataPurpose.PLANNING,
            provider=self._provider.name,
            payload=payload,
            object_summary=(
                "将把你的请求、授权目录的名称和随机目录 ID 发送给模型；"
                "不会发送真实路径、文件名、文件内容或扫描结果。"
            ),
        )

    async def plan(
        self,
        user_goal: str,
        confirmation_id: UUID,
        root_ids: tuple[UUID, ...] | None = None,
    ) -> FileAnalysisPlanningResult:
        """Return a deterministic plan after provider Schema validation."""
        request = self.build_provider_request(user_goal, root_ids)
        self._external_consent.require_approved(
            confirmation_id,
            purpose=ExternalDataPurpose.PLANNING,
            provider=self._provider.name,
            payload=request.model_dump(mode="json"),
        )
        provider_result = await self._provider.create_file_analysis_intent(request)
        plan = self._compiler.compile(user_goal, provider_result.intent)
        return FileAnalysisPlanningResult(
            plan=plan,
            provider=provider_result.provider,
            provider_request_id=provider_result.request_id,
        )
