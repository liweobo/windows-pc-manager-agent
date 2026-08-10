"""Independent safety validation for compiled Stage 1 file-analysis plans."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pc_manager_agent.authorization.service import AuthorizedPathService
from pc_manager_agent.domain.file_analysis import AnalysisType, FileAnalysisPlan
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.safety.path_policy import PathSecurityError, path_is_within
from pc_manager_agent.safety.plan_reviewer import ReviewIssue, SafetyReview, SafetyReviewer
from pc_manager_agent.tools.registry import ToolRegistry


class FileAnalysisSafetyValidator:
    """Ensure compiled semantics, tool manifests, session, and scope still agree."""

    _TOOLS: ClassVar[set[str]] = {
        "file.scan",
        "file.analyze.large",
        "file.analyze.inactive",
        "file.analyze.duplicates",
    }

    def __init__(
        self,
        reviewer: SafetyReviewer,
        registry: ToolRegistry,
        authorization: AuthorizedPathService,
    ) -> None:
        self._reviewer = reviewer
        self._registry = registry
        self._authorization = authorization

    def review(self, plan: FileAnalysisPlan) -> SafetyReview:
        """Return a denial whenever any Stage 1 invariant is inconsistent."""
        base = self._reviewer.review(plan.task_plan)
        issues = list(base.issues)
        task = plan.task_plan
        if task.estimated_impact.files_modified or task.estimated_impact.files_deleted:
            issues.append(
                ReviewIssue(
                    code="read-only-impact-required",
                    message="File analysis cannot modify or delete files",
                )
            )
        mode_marker = f"匹配模式：{plan.match_mode.value}"
        if mode_marker not in task.assumptions:
            issues.append(
                ReviewIssue(
                    code="match-mode-mismatch",
                    message="Match mode is not bound into the confirmed task plan",
                )
            )
        try:
            records = self._authorization.resolve_authorized(plan.authorized_root_ids)
            policy = self._authorization.build_policy(plan.authorized_root_ids)
        except (PermissionError, ValueError) as exc:
            issues.append(ReviewIssue(code="authorization-invalid", message=str(exc)))
            return SafetyReview(approved=False, issues=tuple(issues))
        authorized_roots = {record.path for record in records}
        if set(task.scope.included_paths) != authorized_roots:
            issues.append(
                ReviewIssue(
                    code="authorized-scope-mismatch",
                    message="Plan scope differs from the selected authorized root IDs",
                )
            )

        expected_analysis_tools = {
            AnalysisType.LARGE_FILES: "file.analyze.large",
            AnalysisType.INACTIVE_FILES: "file.analyze.inactive",
            AnalysisType.DUPLICATES: "file.analyze.duplicates",
        }
        actual_analysis_tools: set[str] = set()
        scan_roots: set[Path] = set()
        for step in task.steps:
            if step.tool_name not in self._TOOLS:
                issues.append(
                    ReviewIssue(
                        code="stage1-tool-denied",
                        message=f"Tool is outside the Stage 1 allow-list: {step.tool_name}",
                        step_id=step.step_id,
                    )
                )
                continue
            manifest = self._registry.manifest(step.tool_name)
            if manifest.risk_level is not RiskLevel.R0 or not manifest.read_only:
                issues.append(
                    ReviewIssue(
                        code="non-read-only-tool",
                        message="Every Stage 1 tool must be registered as read-only R0",
                        step_id=step.step_id,
                    )
                )
            session_value = step.arguments.get("session_id") or step.arguments.get(
                "analysis_session_id"
            )
            if session_value != str(plan.analysis_session_id):
                issues.append(
                    ReviewIssue(
                        code="analysis-session-mismatch",
                        message="Step is not bound to the confirmed analysis session",
                        step_id=step.step_id,
                    )
                )
            if step.tool_name == "file.scan":
                raw_root = step.arguments.get("root")
                if not isinstance(raw_root, str):
                    continue
                root = Path(raw_root)
                scan_roots.add(root)
                try:
                    policy.validate_scan_root(root)
                except PathSecurityError as exc:
                    issues.append(
                        ReviewIssue(
                            code="scan-root-invalid",
                            message=str(exc),
                            step_id=step.step_id,
                        )
                    )
                exclusions = step.arguments.get("excluded_paths", [])
                if not isinstance(exclusions, list) or any(
                    not isinstance(value, str) or not path_is_within(Path(value), root)
                    for value in exclusions
                ):
                    issues.append(
                        ReviewIssue(
                            code="excluded-scope-invalid",
                            message="Every excluded path must remain within its scan root",
                            step_id=step.step_id,
                        )
                    )
            else:
                actual_analysis_tools.add(step.tool_name)
                if (
                    step.tool_name == "file.analyze.large"
                    and step.arguments.get("minimum_size_bytes") != plan.filters.minimum_size_bytes
                ):
                    issues.append(
                        ReviewIssue(
                            code="large-threshold-mismatch",
                            message="Large-file threshold differs from the structured plan",
                            step_id=step.step_id,
                        )
                    )
                if (
                    step.tool_name == "file.analyze.inactive"
                    and step.arguments.get("inactive_days") != plan.filters.inactive_days
                ):
                    issues.append(
                        ReviewIssue(
                            code="inactive-threshold-mismatch",
                            message="Inactive threshold differs from the structured plan",
                            step_id=step.step_id,
                        )
                    )

        if scan_roots != authorized_roots:
            issues.append(
                ReviewIssue(
                    code="scan-step-scope-mismatch",
                    message="Scanner steps do not exactly cover authorized roots",
                )
            )
        expected_tools = {expected_analysis_tools[item] for item in plan.analyses}
        if actual_analysis_tools != expected_tools:
            issues.append(
                ReviewIssue(
                    code="analysis-step-mismatch",
                    message="Analyzer steps differ from the structured analysis selection",
                )
            )
        return SafetyReview(approved=not issues, issues=tuple(issues))
