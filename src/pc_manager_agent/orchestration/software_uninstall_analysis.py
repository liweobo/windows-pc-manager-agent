"""Confirmed Stage 4D1 orchestration that always stops before uninstall execution."""

from __future__ import annotations

from uuid import UUID

from pc_manager_agent.audit.software_uninstall_analysis import (
    SoftwareUninstallAnalysisAuditLogger,
)
from pc_manager_agent.confirmation.models import ConfirmationRequest
from pc_manager_agent.confirmation.software_uninstall_analysis import (
    SoftwareAnalysisConfirmationService,
)
from pc_manager_agent.domain.software_errors import (
    SoftwareAnalysisError,
    SoftwareAnalysisErrorCode,
)
from pc_manager_agent.domain.software_uninstall_analysis import (
    SoftwareAnalysisOutcome,
    SoftwareCapabilityResult,
    SoftwareInspectResult,
    SoftwareInventoryResult,
    SoftwarePreviewResult,
    SoftwareResolveResult,
    SoftwareTargetAcknowledgement,
    SoftwareTargetQuery,
    SoftwareUninstallAnalysisPlan,
    SoftwareUninstallPreview,
)
from pc_manager_agent.safety.software_uninstall_validator import (
    SoftwareSafetyReview,
    SoftwareUninstallSafetyValidator,
)
from pc_manager_agent.safety.software_zero_execution import (
    STAGE_4D1_TOOL_NAMES,
    SoftwareZeroExecutionGuard,
)
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


def is_software_uninstall_analysis_request(user_goal: str) -> bool:
    """Return true only for explicit software/application uninstall wording."""
    normalized = user_goal.casefold()
    chinese = "卸载" in normalized and any(
        marker in normalized for marker in ("软件", "程序", "应用", "app", "工具")
    )
    english = any(
        marker in normalized
        for marker in ("uninstall software", "uninstall app", "remove application")
    )
    return chinese or english


def extract_software_target_name(user_goal: str) -> str | None:
    """Extract a bounded local search term; never interpret it as a command or tool name."""
    value = " ".join(user_goal.strip().split())
    removable = (
        "帮我",
        "请",
        "我想",
        "分析",
        "看看",
        "卸载",
        "软件",
        "程序",
        "应用",
        "uninstall",
        "software",
        "application",
        "app",
        "remove",
    )
    for item in removable:
        value = value.replace(item, "").replace(item.title(), "")
    result = " ".join(value.split()).strip("：:，,。?？!！\"'")
    return result if 1 <= len(result) <= 200 else None


class SoftwareUninstallAnalysisPlanCompiler:
    """Compile one exact or display-name target into the fixed five-tool R0 plan."""

    def __init__(self, max_items: int = 5_000) -> None:
        self._max_items = max_items

    def compile(
        self,
        user_goal: str,
        target_query: SoftwareTargetQuery | None = None,
    ) -> SoftwareUninstallAnalysisPlan:
        """Build a deterministic plan; vague requests fail before inventory access."""
        query = target_query
        if query is None:
            target_name = extract_software_target_name(user_goal)
            if target_name is None:
                raise SoftwareAnalysisError(
                    SoftwareAnalysisErrorCode.TARGET_AMBIGUOUS,
                    "Please name one software product or select one exact software row",
                )
            query = SoftwareTargetQuery(display_name=target_name)
        return SoftwareUninstallAnalysisPlan(
            user_goal=user_goal,
            summary="Prepare a read-only software identity, capability, and impact Preview",
            target_query=query,
            tool_names=STAGE_4D1_TOOL_NAMES,
            max_items=self._max_items,
        )


class SoftwareUninstallAnalysisService:
    """Coordinate plan review, confirmation, five R0 tools, Preview, audit, and STOP."""

    def __init__(
        self,
        compiler: SoftwareUninstallAnalysisPlanCompiler,
        registry: ToolRegistry,
        validator: SoftwareUninstallSafetyValidator,
        zero_guard: SoftwareZeroExecutionGuard,
        confirmation: SoftwareAnalysisConfirmationService,
        audit: SoftwareUninstallAnalysisAuditLogger,
    ) -> None:
        self._compiler = compiler
        self._registry = registry
        self._validator = validator
        self._zero_guard = zero_guard
        self._confirmation = confirmation
        self._audit = audit

    def prepare(
        self,
        user_goal: str,
        target_query: SoftwareTargetQuery | None = None,
    ) -> tuple[SoftwareUninstallAnalysisPlan, SoftwareSafetyReview]:
        """Compile, independently review, and audit a zero-execution analysis plan."""
        plan = self._compiler.compile(user_goal, target_query)
        review = self._validator.review_plan(plan)
        self._audit.plan_reviewed(
            plan, review.approved, tuple(issue.message for issue in review.issues)
        )
        return plan, review

    def request_plan_confirmation(
        self,
        plan: SoftwareUninstallAnalysisPlan,
    ) -> ConfirmationRequest:
        """Request confirmation only after a fresh independent R0 review."""
        review = self._validator.review_plan(plan)
        if not review.approved:
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.ZERO_EXECUTION_VIOLATION,
                "Stage 4D1 plan failed independent safety review",
            )
        return self._confirmation.request_plan(plan)

    def resolve_plan_confirmation(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: SoftwareUninstallAnalysisPlan,
    ) -> ConfirmationRequest:
        """Resolve and audit the exact R0 plan confirmation."""
        resolved = self._confirmation.resolve_plan(confirmation_id, approved, plan)
        self._audit.plan_confirmation(plan, resolved)
        return resolved

    def analyze(
        self,
        plan: SoftwareUninstallAnalysisPlan,
        cancellation: CancellationToken | None = None,
    ) -> SoftwareAnalysisOutcome:
        """Run all R0 boundaries, return candidates or one Preview, and never execute removal."""
        review = self._validator.review_plan(plan)
        if not review.approved:
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.ZERO_EXECUTION_VIOLATION,
                "Stage 4D1 plan changed or failed execution-time review",
            )
        self._confirmation.require_plan_approved(plan)
        token = cancellation or CancellationToken()
        try:
            inventory_result = self._execute_inventory(plan, token)
            self._audit.inventory_completed(plan, inventory_result.inventory)
            resolve_result = self._execute_resolve(plan, token)
            resolution = resolve_result.resolved
            self._audit.target_resolved(plan, resolution)
            if resolution.selected is None:
                return SoftwareAnalysisOutcome(resolution=resolution)
            identity_digest = resolution.selected.identity.canonical_digest()
            inspect_result = self._execute_inspect(plan, identity_digest, token)
            if not inspect_result.found or inspect_result.software is None:
                raise SoftwareAnalysisError(
                    SoftwareAnalysisErrorCode.TARGET_CHANGED,
                    "Software identity changed after target resolution",
                )
            capability_result = self._execute_capability(plan, identity_digest, token)
            preview_result = self._execute_preview(plan, identity_digest, token)
            preview = preview_result.preview
            if inspect_result.software.metadata_digest() != preview.metadata_digest:
                raise SoftwareAnalysisError(
                    SoftwareAnalysisErrorCode.TARGET_CHANGED,
                    "Software metadata changed while the Preview was being prepared",
                )
            if capability_result.capability.canonical_digest() != preview.capability_digest:
                raise SoftwareAnalysisError(
                    SoftwareAnalysisErrorCode.CAPABILITY_CHANGED,
                    "Uninstall capability metadata changed while the Preview was being prepared",
                )
            preview_review = self._validator.review_preview(plan, preview)
            if not preview_review.approved:
                raise SoftwareAnalysisError(
                    SoftwareAnalysisErrorCode.ZERO_EXECUTION_VIOLATION,
                    "Software Preview failed independent safety review",
                )
            self._audit.previewed(plan, preview)
            return SoftwareAnalysisOutcome(resolution=resolution, preview=preview)
        except SoftwareAnalysisError as exc:
            self._audit.failed(plan, "analysis", exc.code.value)
            raise

    def request_target_acknowledgement(
        self,
        plan: SoftwareUninstallAnalysisPlan,
        preview: SoftwareUninstallPreview,
    ) -> SoftwareTargetAcknowledgement:
        """Create a non-authorizing acknowledgement after Preview validation."""
        review = self._validator.review_preview(plan, preview)
        if not review.approved:
            raise SoftwareAnalysisError(
                SoftwareAnalysisErrorCode.ZERO_EXECUTION_VIOLATION,
                "Cannot acknowledge an invalid software Preview",
            )
        return self._confirmation.request_acknowledgement(plan, preview)

    def resolve_target_acknowledgement(
        self,
        acknowledgement_id: UUID,
        acknowledged: bool,
        plan: SoftwareUninstallAnalysisPlan,
        preview: SoftwareUninstallPreview,
    ) -> SoftwareTargetAcknowledgement:
        """Audit target understanding and end the workflow without creating authority."""
        resolved = self._confirmation.resolve_acknowledgement(
            acknowledgement_id, acknowledged, preview
        )
        self._audit.acknowledgement_resolved(plan, resolved)
        return resolved

    def _execute_inventory(
        self,
        plan: SoftwareUninstallAnalysisPlan,
        token: CancellationToken,
    ) -> SoftwareInventoryResult:
        result = self._registry.execute("software.inventory", {"max_items": plan.max_items}, token)
        self._zero_guard.validate_result(result)
        if not isinstance(result, SoftwareInventoryResult):
            raise TypeError("software.inventory returned an unexpected result")
        return result

    def _execute_resolve(
        self,
        plan: SoftwareUninstallAnalysisPlan,
        token: CancellationToken,
    ) -> SoftwareResolveResult:
        result = self._registry.execute(
            "software.resolve",
            {
                "query": plan.target_query.model_dump(mode="json"),
                "max_items": plan.max_items,
            },
            token,
        )
        self._zero_guard.validate_result(result)
        if not isinstance(result, SoftwareResolveResult):
            raise TypeError("software.resolve returned an unexpected result")
        return result

    def _execute_inspect(
        self,
        plan: SoftwareUninstallAnalysisPlan,
        identity_digest: str,
        token: CancellationToken,
    ) -> SoftwareInspectResult:
        result = self._registry.execute(
            "software.inspect",
            {"identity_digest": identity_digest, "max_items": plan.max_items},
            token,
        )
        self._zero_guard.validate_result(result)
        if not isinstance(result, SoftwareInspectResult):
            raise TypeError("software.inspect returned an unexpected result")
        return result

    def _execute_capability(
        self,
        plan: SoftwareUninstallAnalysisPlan,
        identity_digest: str,
        token: CancellationToken,
    ) -> SoftwareCapabilityResult:
        result = self._registry.execute(
            "software.uninstall_capability",
            {"identity_digest": identity_digest, "max_items": plan.max_items},
            token,
        )
        self._zero_guard.validate_result(result)
        if not isinstance(result, SoftwareCapabilityResult):
            raise TypeError("software.uninstall_capability returned an unexpected result")
        return result

    def _execute_preview(
        self,
        plan: SoftwareUninstallAnalysisPlan,
        identity_digest: str,
        token: CancellationToken,
    ) -> SoftwarePreviewResult:
        result = self._registry.execute(
            "software.uninstall_preview",
            {"plan": plan.model_dump(mode="json"), "identity_digest": identity_digest},
            token,
        )
        self._zero_guard.validate_result(result)
        if not isinstance(result, SoftwarePreviewResult):
            raise TypeError("software.uninstall_preview returned an unexpected result")
        return result
