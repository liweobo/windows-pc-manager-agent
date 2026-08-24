"""Deterministic Stage 4D4 eligibility, path, activity, and risk policies."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pc_manager_agent.domain.residual_cleanup import (
    CleanupEligibilityDecision,
    ResidualMaterialSnapshot,
    ResidualPathSafetyDecision,
    ResidualRecentActivityDecision,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.software_residuals import (
    ContextPathEvidence,
    OwnershipConfidence,
    ResidualCandidate,
    ResidualClassification,
    ResidualSource,
    UninstallContext,
    UserDataProtectionLevel,
)
from pc_manager_agent.safety.path_policy import PathPolicy, path_is_within
from pc_manager_agent.safety.residual_scope_policy import ResidualScanScopePolicy

_ELIGIBLE_CLASSES = frozenset(
    {
        ResidualClassification.PROGRAM_RESIDUAL,
        ResidualClassification.CACHE,
        ResidualClassification.LOG,
        ResidualClassification.SHORTCUT,
    }
)
_ALWAYS_BLOCKED_CLASSES = frozenset(
    {
        ResidualClassification.CONFIGURATION,
        ResidualClassification.USER_DATA,
        ResidualClassification.DATABASE,
        ResidualClassification.PLUGIN_OR_EXTENSION,
        ResidualClassification.PACKAGE_USER_DATA,
        ResidualClassification.APPLICATION_STATE,
        ResidualClassification.LICENSE_DATA,
        ResidualClassification.UNKNOWN,
    }
)
_V1_DEFERRED_CLASSES = frozenset(
    {
        ResidualClassification.TEMPORARY_DATA,
        ResidualClassification.SERVICE_RELATED_ARTIFACT,
        ResidualClassification.CRASH_DUMP,
    }
)
_BLOCKED_PROTECTION = frozenset(
    {
        UserDataProtectionLevel.PROTECTED,
        UserDataProtectionLevel.STRONGLY_PROTECTED,
        UserDataProtectionLevel.UNKNOWN,
    }
)


class ResidualCleanupPolicyError(PermissionError):
    """Raised before metadata traversal when an exact candidate path is unsafe."""


class ResidualCleanupPathPolicy:
    """Authorize one report candidate without widening it to parents or siblings."""

    def __init__(
        self,
        scope: ResidualScanScopePolicy,
        *,
        network_path_detector: Callable[[Path], bool] | None = None,
        access_checker: Callable[[Path, int], bool] | None = None,
        user_profile: Path | None = None,
    ) -> None:
        self._scope = scope
        self._network_path_detector = network_path_detector or (lambda _path: False)
        self._access_checker = access_checker or os.access
        profile = user_profile or Path(os.environ.get("USERPROFILE", Path.home()))
        self._profile = Path(os.path.abspath(profile))
        self._windows = Path(os.environ.get("SYSTEMROOT", "C:/Windows"))
        self._program_data = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData"))
        self._program_files = tuple(
            Path(value)
            for value in (
                os.environ.get("PROGRAMFILES", "C:/Program Files"),
                os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"),
            )
            if value
        )

    def validate_candidate(
        self,
        candidate: ResidualCandidate,
        evidence: ContextPathEvidence,
    ) -> tuple[Path, ResidualPathSafetyDecision]:
        """Resolve only the selected report path and return its fresh path decision."""
        reasons: list[str] = []
        if candidate.report_id.int == 0:
            raise ResidualCleanupPolicyError("invalid-report-candidate")
        expected_root = Path(os.path.abspath(os.path.normpath(os.fspath(evidence.path))))
        report_root = Path(os.path.abspath(os.path.normpath(os.fspath(candidate.scan_root))))
        selected = Path(os.path.abspath(os.path.normpath(os.fspath(candidate.path))))
        if report_root != expected_root or candidate.source is not evidence.source:
            raise ResidualCleanupPolicyError("candidate-context-evidence-mismatch")
        if not path_is_within(selected, expected_root):
            raise ResidualCleanupPolicyError("candidate-outside-exact-context-root")
        try:
            selected = self._scope.validate_existing_root(selected)
        except PermissionError as exc:
            raise ResidualCleanupPolicyError(str(exc)) from exc
        base = PathPolicy.for_authorized_roots(
            (selected,),
            network_path_detector=self._network_path_detector,
        )
        try:
            selected = base.validate_operation_source(selected)
        except PermissionError as exc:
            raise ResidualCleanupPolicyError(str(exc)) from exc
        shared = self.is_shared(selected, evidence)
        if shared:
            reasons.append("shared-location")
        if path_is_within(selected, self._windows):
            reasons.append("windows-system-path")
        ordinary_access = self._access_checker(selected.parent, os.W_OK)
        if not ordinary_access:
            reasons.append("ordinary-user-delete-access-not-proven")
        return selected, ResidualPathSafetyDecision(
            safe=not reasons,
            exact_context_path=True,
            ordinary_user_access=ordinary_access,
            shared_location=shared,
            reparse_detected=False,
            network_or_unsupported_volume=False,
            reason_codes=tuple(reasons) or ("exact-candidate-path-validated",),
        )

    def entry_rejection_reason(self, path: Path, selected_root: Path) -> str | None:
        """Return why one descendant makes the entire selected tree ineligible."""
        reason = self._scope.entry_rejection_reason(path, selected_root)
        if reason is not None:
            return reason
        policy = PathPolicy.for_authorized_roots(
            (selected_root,),
            network_path_detector=self._network_path_detector,
        )
        if policy.is_forbidden(path):
            return "protected-descendant"
        if self._network_path_detector(path):
            return "network-descendant"
        return None

    def is_shared(self, path: Path, evidence: ContextPathEvidence) -> bool:
        """Detect explicit and conservative common-location signals."""
        if evidence.shared_location:
            return True
        components = {part.casefold() for part in path.parts}
        if components.intersection({"common files", "shared", "public"}):
            return True
        if path_is_within(path, self._program_data):
            relative = path.relative_to(self._program_data)
            # ProgramData\Vendor is too broad; Vendor\Product\Cache may be assessed.
            if len(relative.parts) < 3:
                return True
        for root in self._program_files:
            if path_is_within(path, root):
                relative = path.relative_to(root)
                if not relative.parts or relative.parts[0].casefold() == "common files":
                    return True
        return False


class ResidualRecentModificationPolicy:
    """Block any selected tree whose newest metadata postdates uninstall completion."""

    def evaluate(
        self,
        context: UninstallContext,
        material: ResidualMaterialSnapshot,
    ) -> ResidualRecentActivityDecision:
        """Compare the complete fresh tree to the durable uninstall completion time."""
        completed = context.uninstall_completed_at
        if completed is None:
            raise ResidualCleanupPolicyError("uninstall-completion-time-unavailable")
        newest = datetime.fromtimestamp(material.maximum_modified_ns / 1_000_000_000, tz=UTC)
        blocked = newest > completed
        return ResidualRecentActivityDecision(
            blocked=blocked,
            uninstall_completed_at=completed,
            newest_modified_at=newest,
            reason_codes=(
                ("modified-after-uninstall",)
                if blocked
                else ("no-post-uninstall-modification-detected",)
            ),
        )


class CleanupEligibilityPolicy:
    """Make cleanup eligibility a local finite decision independent from the LLM."""

    def evaluate(
        self,
        *,
        context: UninstallContext,
        evidence: ContextPathEvidence,
        classification: ResidualClassification,
        ownership: OwnershipConfidence,
        protection: UserDataProtectionLevel,
        material: ResidualMaterialSnapshot,
        path_safety: ResidualPathSafetyDecision,
        recent_activity: ResidualRecentActivityDecision,
        recycle_bin_available: bool,
    ) -> tuple[CleanupEligibilityDecision, tuple[str, ...]]:
        """Return ELIGIBLE only when every independent fresh gate passes."""
        reasons: list[str] = []
        if not context.verified_removed or not context.context_complete:
            reasons.append("uninstall-not-verified-removed")
        if classification in _ALWAYS_BLOCKED_CLASSES:
            reasons.append(f"classification-always-blocked-{classification.value}")
        elif classification in _V1_DEFERRED_CLASSES:
            reasons.append(f"classification-deferred-v1-{classification.value}")
        elif classification not in _ELIGIBLE_CLASSES:
            reasons.append("classification-not-allow-listed")
        if ownership is not OwnershipConfidence.HIGH:
            reasons.append("ownership-confidence-not-high")
        if protection in _BLOCKED_PROTECTION:
            reasons.append(f"protection-blocked-{protection.value}")
        if not path_safety.safe:
            reasons.extend(path_safety.reason_codes)
        if recent_activity.blocked:
            reasons.extend(recent_activity.reason_codes)
        if not recycle_bin_available:
            reasons.append("recycle-bin-unavailable")
        if material.forbidden_descendant_count:
            reasons.append("tree-contains-forbidden-descendant")
        forbidden_tree_classes = {
            item.classification
            for item in material.classification_counts
            if item.classification in _ALWAYS_BLOCKED_CLASSES
            or item.classification in _V1_DEFERRED_CLASSES
        }
        reasons.extend(
            f"tree-contains-{classification.value}"
            for classification in sorted(forbidden_tree_classes, key=lambda item: item.value)
        )
        if classification is ResidualClassification.SHORTCUT:
            if evidence.source is not ResidualSource.SHORTCUT:
                reasons.append("shortcut-source-not-exact")
            if evidence.related_target_path is None:
                reasons.append("shortcut-target-evidence-missing")
            elif evidence.related_target_path.exists():
                reasons.append("shortcut-target-still-present")
        if classification is ResidualClassification.CACHE and evidence.source not in {
            ResidualSource.KNOWN_APP_DATA,
            ResidualSource.INSTALL_LOCATION,
        }:
            reasons.append("cache-path-is-not-app-specific")
        if classification is ResidualClassification.LOG and evidence.source not in {
            ResidualSource.KNOWN_APP_DATA,
            ResidualSource.INSTALL_LOCATION,
        }:
            reasons.append("log-path-is-not-app-specific")
        if not reasons:
            return CleanupEligibilityDecision.ELIGIBLE, ("all-fresh-cleanup-gates-passed",)
        if reasons == ["ownership-confidence-not-high"]:
            return CleanupEligibilityDecision.MANUAL_REVIEW, tuple(reasons)
        return CleanupEligibilityDecision.BLOCKED, tuple(dict.fromkeys(reasons))


class CleanupRiskPolicy:
    """Promote large batches to R2_HIGH_IMPACT using explicit configuration."""

    def __init__(
        self,
        *,
        max_normal_item_count: int,
        max_normal_object_count: int,
        max_normal_total_size: int,
        max_normal_single_item_size: int,
    ) -> None:
        limits = (
            max_normal_item_count,
            max_normal_object_count,
            max_normal_total_size,
            max_normal_single_item_size,
        )
        if min(limits) <= 0:
            raise ValueError("Residual cleanup risk thresholds must be positive")
        self._max_items = max_normal_item_count
        self._max_objects = max_normal_object_count
        self._max_total = max_normal_total_size
        self._max_single = max_normal_single_item_size

    def classify(
        self,
        *,
        item_count: int,
        object_count: int,
        total_size: int,
        largest_item: int,
    ) -> RiskLevel:
        """Return R2_HIGH_IMPACT when any documented normal threshold is exceeded."""
        if (
            item_count > self._max_items
            or object_count > self._max_objects
            or total_size > self._max_total
            or largest_item > self._max_single
        ):
            return RiskLevel.R2_HIGH_IMPACT
        return RiskLevel.R2
