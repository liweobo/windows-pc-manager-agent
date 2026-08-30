"""Deterministic eligibility, path, activity, and risk rules for Stage 4E2."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.system_cleanup_execution import (
    CleanupActivityDecision,
    CleanupAdapterType,
    CleanupEligibilityDecision,
    CleanupMaterialSnapshot,
    CleanupPathSafetyDecision,
)
from pc_manager_agent.domain.system_optimization import (
    CleanupCategory,
    OptimizationConfidence,
    ProtectionLevel,
)
from pc_manager_agent.safety.path_policy import PathPolicy, path_is_within

_DIRECT_SOURCE_CATEGORIES = {
    "current-user-temp": CleanupCategory.USER_TEMP,
    "directx-shader-cache": CleanupCategory.APPLICATION_CACHE,
    "current-user-crash-dumps": CleanupCategory.CRASH_DUMP,
}
_DEFERRED_CATEGORIES = frozenset(
    {
        CleanupCategory.SYSTEM_TEMP,
        CleanupCategory.BROWSER_CACHE,
        CleanupCategory.WINDOWS_UPDATE_CANDIDATE,
        CleanupCategory.DELIVERY_OPTIMIZATION_CACHE,
        CleanupCategory.RECYCLE_BIN_CONTENT,
    }
)
_BLOCKED_CATEGORIES = frozenset(
    {
        CleanupCategory.INSTALLER_CACHE_CANDIDATE,
        CleanupCategory.LARGE_FILE,
        CleanupCategory.INACTIVE_LARGE_FILE,
        CleanupCategory.DUPLICATE_FILE,
        CleanupCategory.UNKNOWN,
    }
)


class SystemCleanupPolicyError(PermissionError):
    """Raised when exact Stage 4E2 path authority cannot be proven."""


class SystemCleanupPathPolicy:
    """Authorize only descendants of one exact known current-user cleanup root."""

    def __init__(
        self,
        known_roots: dict[str, Path],
        *,
        network_path_detector: Callable[[Path], bool] | None = None,
        user_profile: Path | None = None,
    ) -> None:
        profile = user_profile or Path(os.environ.get("USERPROFILE", Path.home()))
        self._profile = Path(os.path.abspath(os.path.normpath(os.fspath(profile))))
        self._known_roots = {
            source: Path(os.path.abspath(os.path.normpath(os.fspath(path))))
            for source, path in known_roots.items()
        }
        self._network = network_path_detector or (lambda _path: False)

    @property
    def known_roots(self) -> dict[str, Path]:
        """Return a copy of the finite source-to-root allowlist."""
        return dict(self._known_roots)

    def validate_report_root(self, source: str, path: Path, category: CleanupCategory) -> Path:
        """Require the report root to equal the configured root and category pair."""
        expected_category = _DIRECT_SOURCE_CATEGORIES.get(source)
        expected_root = self._known_roots.get(source)
        normalized = Path(os.path.abspath(os.path.normpath(os.fspath(path))))
        if (
            expected_root is None
            or expected_category is not category
            or normalized != expected_root
        ):
            raise SystemCleanupPolicyError("Stage 4E1 candidate is not an exact V1 known root")
        if not path_is_within(normalized, self._profile):
            raise SystemCleanupPolicyError("Cleanup root is outside the current-user profile")
        policy = PathPolicy.for_authorized_roots(
            (normalized,),
            network_path_detector=self._network,
        )
        try:
            return policy.validate_scan_root(normalized)
        except PermissionError as exc:
            raise SystemCleanupPolicyError(str(exc)) from exc

    def validate_item(self, item: Path, root: Path) -> tuple[Path, CleanupPathSafetyDecision]:
        """Resolve one discovered child and return independently bindable path evidence."""
        normalized_root = Path(os.path.abspath(os.path.normpath(os.fspath(root))))
        policy = PathPolicy.for_authorized_roots(
            (normalized_root,),
            network_path_detector=self._network,
        )
        try:
            normalized = policy.validate_operation_source(item)
        except PermissionError as exc:
            raise SystemCleanupPolicyError(str(exc)) from exc
        other_user = not path_is_within(normalized, self._profile)
        shared = any(
            part.casefold() in {"public", "shared", "common files"} for part in normalized.parts
        )
        sensitive = policy.is_forbidden(normalized)
        network = self._network(normalized)
        safe = (
            path_is_within(normalized, normalized_root)
            and normalized != normalized_root
            and not other_user
            and not shared
            and not sensitive
            and not network
        )
        reasons: list[str] = []
        if other_user:
            reasons.append("other-user-data")
        if shared:
            reasons.append("shared-location")
        if sensitive:
            reasons.append("sensitive-path")
        if network:
            reasons.append("network-or-unsupported-volume")
        if normalized == normalized_root:
            reasons.append("known-root-itself-cannot-be-selected")
        return normalized, CleanupPathSafetyDecision(
            safe=safe,
            current_user_scope=not other_user,
            exact_known_root=path_is_within(normalized, normalized_root),
            other_user=other_user,
            shared_location=shared,
            sensitive_path=sensitive,
            reparse_detected=False,
            network_or_unsupported_volume=network,
            reason_codes=tuple(reasons) or ("exact-known-current-user-child",),
        )

    def validate_entry(self, path: Path, root: Path) -> Path:
        """Reject every descendant redirect, protected path, and scope escape."""
        policy = PathPolicy.for_authorized_roots(
            (root,),
            network_path_detector=self._network,
        )
        reason = policy.entry_rejection_reason(path)
        if reason is not None or self._network(path):
            raise SystemCleanupPolicyError(reason or "network-descendant")
        if not path_is_within(path, root):
            raise SystemCleanupPolicyError("cleanup-descendant-left-selected-root")
        return path


class CleanupRecentActivityPolicy:
    """Treat any recent metadata change as blocking, never as a deletion hint."""

    def __init__(self, *, minimum_age_days: int = 7) -> None:
        if minimum_age_days <= 0:
            raise ValueError("Cleanup minimum age must be positive")
        self._minimum_age = timedelta(days=minimum_age_days)

    def evaluate(
        self,
        material: CleanupMaterialSnapshot,
        *,
        delete_access_available: bool,
        active_installer_detected: bool,
        now: datetime | None = None,
    ) -> CleanupActivityDecision:
        """Bind recency, ordinary delete access, and global installer activity."""
        _ = now or datetime.now(UTC)
        reasons: list[str] = []
        if material.recently_modified_count:
            reasons.append("recent-metadata-change")
        if not delete_access_available:
            reasons.append("locked-or-delete-access-unavailable")
        if active_installer_detected:
            reasons.append("installer-transaction-active")
        return CleanupActivityDecision(
            recently_modified=bool(material.recently_modified_count),
            delete_access_available=delete_access_available,
            active_installer_detected=active_installer_detected,
            blocked=bool(reasons),
            reason_codes=tuple(reasons) or ("no-blocking-activity-detected",),
        )

    def cutoff(self, now: datetime | None = None) -> datetime:
        """Return the inclusive UTC cutoff used during material traversal."""
        return (now or datetime.now(UTC)) - self._minimum_age


class SystemCleanupEligibilityPolicy:
    """Return ELIGIBLE only when every finite local gate agrees."""

    def evaluate(
        self,
        *,
        category: CleanupCategory,
        source: str,
        confidence: OptimizationConfidence,
        protection: ProtectionLevel,
        material: CleanupMaterialSnapshot,
        path_safety: CleanupPathSafetyDecision,
        activity: CleanupActivityDecision,
        recycle_bin_available: bool,
    ) -> tuple[CleanupEligibilityDecision, CleanupAdapterType, tuple[str, ...]]:
        """Classify one exact fresh object without accepting model judgment."""
        if category in _DEFERRED_CATEGORIES:
            return (
                CleanupEligibilityDecision.DEFERRED,
                CleanupAdapterType.DEFERRED,
                (f"category-deferred-v1-{category.value}",),
            )
        if category in _BLOCKED_CATEGORIES:
            adapter = (
                CleanupAdapterType.STAGE2B_HANDOFF
                if category
                in {
                    CleanupCategory.LARGE_FILE,
                    CleanupCategory.INACTIVE_LARGE_FILE,
                    CleanupCategory.DUPLICATE_FILE,
                }
                else CleanupAdapterType.DEFERRED
            )
            return (
                CleanupEligibilityDecision.BLOCKED,
                adapter,
                (f"category-blocked-as-system-cleanup-{category.value}",),
            )
        reasons: list[str] = []
        expected = _DIRECT_SOURCE_CATEGORIES.get(source)
        if expected is not category:
            reasons.append("source-category-not-directly-allow-listed")
        if confidence is not OptimizationConfidence.HIGH:
            reasons.append("fresh-analysis-confidence-not-high")
        if protection is not ProtectionLevel.NONE:
            reasons.append(f"protection-blocked-{protection.value}")
        if not path_safety.safe:
            reasons.extend(path_safety.reason_codes)
        if activity.blocked:
            reasons.extend(activity.reason_codes)
        if material.reparse_count:
            reasons.append("tree-contains-reparse-point")
        if material.sensitive_signal_count:
            reasons.append("tree-contains-protected-data-signal")
        if not recycle_bin_available:
            reasons.append("recycle-bin-unavailable")
        if reasons:
            return (
                CleanupEligibilityDecision.BLOCKED,
                CleanupAdapterType.RECYCLE_BIN_ITEM,
                tuple(dict.fromkeys(reasons)),
            )
        return (
            CleanupEligibilityDecision.ELIGIBLE,
            CleanupAdapterType.RECYCLE_BIN_ITEM,
            ("all-fresh-system-cleanup-gates-passed",),
        )


class SystemCleanupRiskPolicy:
    """Promote ordinary cleanup to R2_HIGH_IMPACT at configured thresholds."""

    def __init__(
        self,
        *,
        max_normal_items: int,
        max_normal_objects: int,
        max_normal_total_bytes: int,
        max_normal_single_item_bytes: int,
    ) -> None:
        values = (
            max_normal_items,
            max_normal_objects,
            max_normal_total_bytes,
            max_normal_single_item_bytes,
        )
        if min(values) <= 0:
            raise ValueError("Cleanup risk thresholds must be positive")
        self._items, self._objects, self._total, self._single = values

    def classify(
        self,
        *,
        item_count: int,
        object_count: int,
        total_bytes: int,
        largest_item_bytes: int,
    ) -> RiskLevel:
        """Return R2_HIGH_IMPACT when any normal threshold is exceeded."""
        if (
            item_count > self._items
            or object_count > self._objects
            or total_bytes > self._total
            or largest_item_bytes > self._single
        ):
            return RiskLevel.R2_HIGH_IMPACT
        return RiskLevel.R2
