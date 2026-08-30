"""Fresh object discovery and metadata-only revalidation for Stage 4E2."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from pc_manager_agent.domain.file_operations import FileObjectKind, FileState
from pc_manager_agent.domain.software_uninstall_analysis import canonical_digest
from pc_manager_agent.domain.system_cleanup_execution import (
    CleanupActivityDecision,
    CleanupAdapterType,
    CleanupEligibilityDecision,
    CleanupExecutionCandidate,
    CleanupMaterialSnapshot,
    CleanupObjectIdentity,
    CleanupPathSafetyDecision,
    CleanupRecoverabilityDecision,
    CleanupRecoveryLevel,
    SystemCleanupAssessment,
    SystemCleanupRequest,
)
from pc_manager_agent.domain.system_optimization import (
    CleanupCandidate,
    CleanupCategory,
    CleanupEvidenceOrigin,
    CleanupSafetyClassification,
    OptimizationConfidence,
    ProtectionLevel,
)
from pc_manager_agent.domain.trash import TrashObjectSnapshot
from pc_manager_agent.orchestration.optimization_report_store import (
    OptimizationReportSessionStore,
)
from pc_manager_agent.platform_support.base import FileOperationPlatform, RecycleBinPlatform
from pc_manager_agent.platform_support.system_cleanup import CleanupActivityProbe
from pc_manager_agent.safety.system_cleanup_policy import (
    CleanupRecentActivityPolicy,
    SystemCleanupEligibilityPolicy,
    SystemCleanupPathPolicy,
    SystemCleanupPolicyError,
)
from pc_manager_agent.tools.manifest import CancellationToken

_HIDDEN = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
_SYSTEM = getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_OFFLINE = getattr(stat, "FILE_ATTRIBUTE_OFFLINE", 0x1000)
_DATABASE_SUFFIXES = frozenset({".db", ".db3", ".sqlite", ".sqlite3", ".mdb", ".accdb"})
_CONFIG_SUFFIXES = frozenset(
    {".cfg", ".conf", ".config", ".ini", ".json", ".toml", ".yaml", ".yml", ".xml"}
)
_SENSITIVE_COMPONENTS = frozenset(
    {
        ".ssh",
        "cookies",
        "sessions",
        "login data",
        "web data",
        "passwords",
        "credentials",
        "wallets",
        "database",
        "databases",
        "config",
        "settings",
        "profile",
        "user data",
        "documents",
    }
)
_DIRECT_CATEGORIES = frozenset(
    {
        CleanupCategory.USER_TEMP,
        CleanupCategory.APPLICATION_CACHE,
        CleanupCategory.LOG,
        CleanupCategory.CRASH_DUMP,
    }
)


class SystemCleanupRevalidationError(RuntimeError):
    """Raised when fresh selected-object evidence cannot be completed safely."""


class FreshCleanupCandidateRevalidator:
    """Resolve only session-local report IDs, then discover exact child objects afresh."""

    def __init__(
        self,
        reports: OptimizationReportSessionStore,
        path_policy: SystemCleanupPathPolicy,
        eligibility: SystemCleanupEligibilityPolicy,
        recent_activity: CleanupRecentActivityPolicy,
        identity_platform: FileOperationPlatform,
        recycle_platform: RecycleBinPlatform,
        activity_probe: CleanupActivityProbe,
        *,
        active_installer: Callable[[], bool] | None = None,
        max_selected_candidates: int = 20,
        max_discovered_items: int = 1_000,
        max_contained_objects: int = 10_000,
        max_total_bytes: int = 50 * 1024**3,
    ) -> None:
        limits = (
            max_selected_candidates,
            max_discovered_items,
            max_contained_objects,
            max_total_bytes,
        )
        if min(limits) <= 0:
            raise ValueError("System cleanup revalidation limits must be positive")
        self._reports = reports
        self._paths = path_policy
        self._eligibility = eligibility
        self._recent = recent_activity
        self._identity = identity_platform
        self._recycle = recycle_platform
        self._activity = activity_probe
        self._active_installer = active_installer or (lambda: False)
        self._max_selected = max_selected_candidates
        self._max_discovered = max_discovered_items
        self._max_objects = max_contained_objects
        self._max_bytes = max_total_bytes

    def assess(
        self,
        request: SystemCleanupRequest,
        cancellation: CancellationToken | None = None,
    ) -> SystemCleanupAssessment:
        """Freshly inspect only explicit Stage 4E1 candidate IDs and retain every denial."""
        if len(request.selected_candidate_ids) > self._max_selected:
            raise SystemCleanupRevalidationError(
                f"Select at most {self._max_selected} Stage 4E1 candidates"
            )
        token = cancellation or CancellationToken()
        report = self._reports.get(request.source_report_id)
        by_id = {candidate.candidate_id: candidate for candidate in report.cleanup_candidates}
        if set(request.selected_candidate_ids) - set(by_id):
            raise SystemCleanupRevalidationError(
                "Cleanup selection contains an unknown or cross-report candidate"
            )
        items: list[CleanupExecutionCandidate] = []
        total_objects = total_bytes = 0
        for candidate_id in request.selected_candidate_ids:
            if token.cancellation_requested():
                raise SystemCleanupRevalidationError("System cleanup fresh scan cancelled")
            candidate = by_id[candidate_id]
            discovered = self._assess_report_candidate(
                report.report_id,
                candidate,
                token,
                item_budget=self._max_discovered - len(items),
            )
            items.extend(discovered)
            if len(items) > self._max_discovered:
                raise SystemCleanupRevalidationError("Cleanup discovery item limit exceeded")
            for item in discovered:
                if item.material is None:
                    continue
                total_objects += item.material.tree.object_count
                total_bytes += item.material.tree.total_size_bytes
            if total_objects > self._max_objects or total_bytes > self._max_bytes:
                raise SystemCleanupRevalidationError(
                    "Cleanup discovery exceeded the hard object or byte limit"
                )
        if not items:
            raise SystemCleanupRevalidationError("No fresh cleanup object was discovered")
        return SystemCleanupAssessment(
            request=request,
            items=tuple(items),
            eligible_count=sum(
                item.eligibility is CleanupEligibilityDecision.ELIGIBLE for item in items
            ),
            blocked_count=sum(
                item.eligibility is CleanupEligibilityDecision.BLOCKED for item in items
            ),
            manual_review_count=sum(
                item.eligibility is CleanupEligibilityDecision.MANUAL_REVIEW for item in items
            ),
            deferred_count=sum(
                item.eligibility is CleanupEligibilityDecision.DEFERRED for item in items
            ),
        )

    def require_unchanged(
        self,
        expected: CleanupExecutionCandidate,
        cancellation: CancellationToken | None = None,
    ) -> CleanupExecutionCandidate:
        """Repeat discovery and require the exact same identity, material, and policy digest."""
        if expected.path is None:
            raise SystemCleanupRevalidationError("Cleanup item path is unavailable")
        token = cancellation or CancellationToken()
        report = self._reports.get(expected.source_report_id)
        matches = tuple(
            candidate
            for candidate in report.cleanup_candidates
            if candidate.candidate_id == expected.source_candidate_id
        )
        if len(matches) != 1 or matches[0].path is None:
            raise SystemCleanupRevalidationError("Cleanup source candidate is unavailable")
        source_candidate = matches[0]
        source_root = source_candidate.path
        if source_root is None:
            raise SystemCleanupRevalidationError("Cleanup source root is unavailable")
        root = self._paths.validate_report_root(
            source_candidate.source,
            source_root,
            source_candidate.category,
        )
        current = self._assess_object(
            report.report_id,
            source_candidate,
            root,
            expected.path,
            token,
        )
        if (
            current.eligibility is not CleanupEligibilityDecision.ELIGIBLE
            or current.item_ref != expected.item_ref
            or current.invariant_digest() != expected.invariant_digest()
        ):
            raise SystemCleanupRevalidationError(
                "Cleanup identity, material, classification, or safety evidence changed"
            )
        return current

    def _assess_report_candidate(
        self,
        report_id: UUID,
        candidate: CleanupCandidate,
        cancellation: CancellationToken,
        *,
        item_budget: int,
    ) -> tuple[CleanupExecutionCandidate, ...]:
        if item_budget <= 0:
            raise SystemCleanupRevalidationError("Cleanup discovery item limit exceeded")
        reference = candidate.source_reference
        if reference is not None and reference.origin is CleanupEvidenceOrigin.STAGE4D3_REPORT:
            return (self._handoff(report_id, candidate, CleanupAdapterType.STAGE4D4_HANDOFF),)
        if reference is not None and reference.origin is CleanupEvidenceOrigin.STAGE1_REPORT:
            return (self._handoff(report_id, candidate, CleanupAdapterType.STAGE2B_HANDOFF),)
        if candidate.category is CleanupCategory.RECYCLE_BIN_CONTENT:
            return (
                self._non_direct(
                    report_id,
                    candidate,
                    CleanupEligibilityDecision.DEFERRED,
                    CleanupAdapterType.RECYCLE_BIN_EMPTY,
                    "use-independent-recycle-bin-empty-workflow",
                ),
            )
        if candidate.category not in _DIRECT_CATEGORIES:
            decision, adapter, reasons = self._eligibility.evaluate(
                category=candidate.category,
                source=candidate.source,
                confidence=candidate.confidence,
                protection=candidate.protection_level,
                material=self._placeholder_material(candidate),
                path_safety=self._placeholder_path_safety(),
                activity=self._placeholder_activity(),
                recycle_bin_available=False,
            )
            return (self._non_direct(report_id, candidate, decision, adapter, reasons[0]),)
        if (
            reference is None
            or reference.origin is not CleanupEvidenceOrigin.KNOWN_LOCATION
            or candidate.path is None
        ):
            return (
                self._non_direct(
                    report_id,
                    candidate,
                    CleanupEligibilityDecision.BLOCKED,
                    CleanupAdapterType.DEFERRED,
                    "missing-known-location-provenance",
                ),
            )
        try:
            root = self._paths.validate_report_root(
                candidate.source,
                candidate.path,
                candidate.category,
            )
            children: list[Path] = []
            with os.scandir(root) as entries:
                for entry in entries:
                    if len(children) >= item_budget:
                        raise SystemCleanupRevalidationError(
                            "Cleanup discovery item limit exceeded"
                        )
                    children.append(Path(entry.path))
            children.sort(key=os.fspath)
            if not children:
                return (
                    self._non_direct(
                        report_id,
                        candidate,
                        CleanupEligibilityDecision.DEFERRED,
                        CleanupAdapterType.DEFERRED,
                        "known-location-currently-empty",
                    ),
                )
            return tuple(
                self._assess_object(report_id, candidate, root, child, cancellation)
                for child in children
            )
        except (OSError, PermissionError, SystemCleanupPolicyError, ValueError) as exc:
            return (
                self._non_direct(
                    report_id,
                    candidate,
                    CleanupEligibilityDecision.BLOCKED,
                    CleanupAdapterType.DEFERRED,
                    f"fresh-discovery-failed-{type(exc).__name__.casefold()}",
                ),
            )

    def _assess_object(
        self,
        report_id: UUID,
        source_candidate: CleanupCandidate,
        root: Path,
        path: Path,
        cancellation: CancellationToken,
    ) -> CleanupExecutionCandidate:
        """Build all independent evidence for one freshly discovered exact child."""
        try:
            selected, path_safety = self._paths.validate_item(path, root)
            state = self._identity.inspect(selected)
            material, delete_access = self._snapshot(
                selected,
                source_candidate.category,
                cancellation,
            )
            final_state = self._identity.inspect(selected)
            if not final_state.unchanged_since(state) or not state.unchanged_since(
                material.tree.root_state
            ):
                raise SystemCleanupPolicyError("root-changed-during-fresh-snapshot")
            protection = (
                ProtectionLevel.STRONGLY_PROTECTED
                if material.sensitive_signal_count
                else ProtectionLevel.NONE
            )
            activity = self._recent.evaluate(
                material,
                delete_access_available=delete_access,
                active_installer_detected=self._active_installer(),
            )
            capability = self._recycle.capability(selected)
            decision, adapter, reasons = self._eligibility.evaluate(
                category=source_candidate.category,
                source=source_candidate.source,
                confidence=OptimizationConfidence.HIGH,
                protection=protection,
                material=material,
                path_safety=path_safety,
                activity=activity,
                recycle_bin_available=capability.available,
            )
            recoverability = (
                CleanupRecoverabilityDecision(
                    capability=capability,
                    reason_codes=("windows-recycle-bin-manual-recovery",),
                )
                if capability.available
                else None
            )
            volume_root = capability.volume_root or Path(selected.anchor)
            identity = CleanupObjectIdentity(
                resolved_path=selected,
                state=state,
                volume_root=volume_root,
            )
            return CleanupExecutionCandidate(
                item_ref=self._item_ref(report_id, source_candidate.candidate_id, state),
                source_report_id=report_id,
                source_candidate_id=source_candidate.candidate_id,
                path=selected,
                fresh_identity=identity,
                material=material,
                category=source_candidate.category,
                source=source_candidate.source,
                safety_classification=(
                    CleanupSafetyClassification.LOW_RISK_CANDIDATE
                    if decision is CleanupEligibilityDecision.ELIGIBLE
                    else CleanupSafetyClassification.BLOCKED
                ),
                protection_level=protection,
                analysis_confidence=OptimizationConfidence.HIGH,
                eligibility=decision,
                observed_size_bytes=material.tree.total_size_bytes,
                item_count=material.tree.object_count,
                recoverability=(
                    CleanupRecoveryLevel.MANUAL
                    if capability.available
                    else CleanupRecoveryLevel.NONE
                ),
                path_safety=path_safety,
                activity=activity,
                recoverability_evidence=recoverability,
                risk_flags=tuple(
                    code
                    for code in activity.reason_codes
                    if code != "no-blocking-activity-detected"
                ),
                reason_codes=reasons,
                cleanup_adapter_type=adapter,
            )
        except (OSError, PermissionError, SystemCleanupPolicyError, ValueError) as exc:
            return self._non_direct(
                report_id,
                source_candidate,
                CleanupEligibilityDecision.BLOCKED,
                CleanupAdapterType.RECYCLE_BIN_ITEM,
                f"fresh-object-validation-failed-{type(exc).__name__.casefold()}",
                path=path,
            )

    def _snapshot(
        self,
        source: Path,
        category: CleanupCategory,
        cancellation: CancellationToken,
    ) -> tuple[CleanupMaterialSnapshot, bool]:
        root_state = self._identity.inspect(source)
        stack = [source]
        rows: list[tuple[object, ...]] = []
        file_count = directory_count = total_bytes = largest = 0
        hidden = system = reparse = offline = recent = sensitive = 0
        delete_access = True
        cutoff_ns = int(self._recent.cutoff().timestamp() * 1_000_000_000)
        while stack:
            if cancellation.cancellation_requested():
                raise SystemCleanupRevalidationError("Cleanup material snapshot cancelled")
            current = stack.pop()
            self._paths.validate_entry(current, source)
            state = self._identity.inspect(current)
            relative = "." if current == source else current.relative_to(source).as_posix()
            signal = self._protected_signal(current, category, state.kind)
            sensitive += int(signal is not None)
            recent += int(state.modified_ns > cutoff_ns)
            delete_access = delete_access and self._activity.delete_access_available(current)
            hidden += int(bool(state.attributes & _HIDDEN))
            system += int(bool(state.attributes & _SYSTEM))
            reparse += int(bool(state.attributes & _REPARSE))
            offline += int(bool(state.attributes & _OFFLINE))
            if state.kind is FileObjectKind.FILE:
                file_count += 1
                total_bytes += state.size_bytes
                largest = max(largest, state.size_bytes)
            else:
                directory_count += 1
                with os.scandir(current) as entries:
                    children = sorted((Path(entry.path) for entry in entries), reverse=True)
                stack.extend(children)
            rows.append(
                (
                    relative,
                    state.kind.value,
                    state.volume_serial,
                    state.file_id.casefold(),
                    state.size_bytes,
                    state.created_ns,
                    state.modified_ns,
                    state.attributes,
                    signal,
                )
            )
            if len(rows) > self._max_objects:
                raise SystemCleanupPolicyError("cleanup-preview-object-limit-exceeded")
            if total_bytes > self._max_bytes:
                raise SystemCleanupPolicyError("cleanup-preview-byte-limit-exceeded")
        final_root = self._identity.inspect(source)
        if not final_root.unchanged_since(root_state):
            raise SystemCleanupPolicyError("root-changed-during-material-snapshot")
        encoded = json.dumps(sorted(rows), ensure_ascii=False, separators=(",", ":"))
        tree = TrashObjectSnapshot(
            source=source,
            root_state=root_state,
            tree_digest=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            object_count=len(rows),
            total_size_bytes=total_bytes,
            largest_item_bytes=largest,
            hidden_count=hidden,
            system_count=system,
            reparse_count=reparse,
            offline_count=offline,
        )
        return (
            CleanupMaterialSnapshot(
                tree=tree,
                file_count=file_count,
                directory_count=directory_count,
                recently_modified_count=recent,
                hidden_count=hidden,
                reparse_count=reparse,
                sensitive_signal_count=sensitive,
                classification_digest=canonical_digest(rows),
            ),
            delete_access,
        )

    @staticmethod
    def _protected_signal(
        path: Path,
        category: CleanupCategory,
        kind: FileObjectKind,
    ) -> str | None:
        components = {part.casefold() for part in path.parts}
        suffix = path.suffix.casefold()
        if components.intersection(_SENSITIVE_COMPONENTS):
            return "protected-component"
        if suffix in _DATABASE_SUFFIXES:
            return "database-suffix"
        if suffix in _CONFIG_SUFFIXES:
            return "configuration-suffix"
        if (
            category is CleanupCategory.CRASH_DUMP
            and kind is FileObjectKind.FILE
            and suffix not in {".dmp", ".mdmp"}
        ):
            return "non-dump-object-in-crash-dump-root"
        return None

    @staticmethod
    def _item_ref(report_id: UUID, candidate_id: UUID, state: FileState) -> UUID:
        typed = state
        value = (
            f"stage4e2:{report_id}:{candidate_id}:{str(typed.path).casefold()}:"
            f"{typed.volume_serial}:{typed.file_id.casefold()}:{typed.kind.value}"
        )
        return uuid5(NAMESPACE_URL, value)

    @staticmethod
    def _handoff(
        report_id: UUID,
        candidate: CleanupCandidate,
        adapter: CleanupAdapterType,
    ) -> CleanupExecutionCandidate:
        reference = candidate.source_reference
        if reference is None:
            raise SystemCleanupRevalidationError("Cleanup hand-off provenance is absent")
        return CleanupExecutionCandidate(
            source_report_id=report_id,
            source_candidate_id=candidate.candidate_id,
            path=candidate.path,
            category=candidate.category,
            source=candidate.source,
            safety_classification=candidate.safety_classification,
            protection_level=candidate.protection_level,
            analysis_confidence=candidate.confidence,
            eligibility=CleanupEligibilityDecision.DEFERRED,
            observed_size_bytes=candidate.observed_size_bytes,
            item_count=candidate.item_count,
            recoverability=CleanupRecoveryLevel.MANUAL,
            reason_codes=(
                "route-through-stage4d4-fresh-cleanup"
                if adapter is CleanupAdapterType.STAGE4D4_HANDOFF
                else "route-through-stage2b-personal-file-workflow",
            ),
            cleanup_adapter_type=adapter,
            upstream_report_id=reference.upstream_report_id,
            upstream_candidate_id=reference.upstream_candidate_id,
        )

    @staticmethod
    def _non_direct(
        report_id: UUID,
        candidate: CleanupCandidate,
        decision: CleanupEligibilityDecision,
        adapter: CleanupAdapterType,
        reason: str,
        *,
        path: Path | None = None,
    ) -> CleanupExecutionCandidate:
        return CleanupExecutionCandidate(
            source_report_id=report_id,
            source_candidate_id=candidate.candidate_id,
            path=path or candidate.path,
            category=candidate.category,
            source=candidate.source,
            safety_classification=(
                CleanupSafetyClassification.BLOCKED
                if decision is CleanupEligibilityDecision.BLOCKED
                else candidate.safety_classification
            ),
            protection_level=(
                ProtectionLevel.UNKNOWN
                if decision is CleanupEligibilityDecision.BLOCKED
                else candidate.protection_level
            ),
            analysis_confidence=OptimizationConfidence.UNKNOWN,
            eligibility=decision,
            observed_size_bytes=candidate.observed_size_bytes,
            item_count=candidate.item_count,
            recoverability=CleanupRecoveryLevel.NONE,
            reason_codes=(reason,),
            cleanup_adapter_type=adapter,
        )

    @staticmethod
    def _placeholder_material(candidate: CleanupCandidate) -> CleanupMaterialSnapshot:
        path = candidate.path or Path("C:/unavailable")
        state = FileState(
            path=path,
            kind=FileObjectKind.FILE,
            volume_serial=0,
            file_id="unavailable",
            size_bytes=0,
            created_ns=0,
            modified_ns=0,
            attributes=0,
        )
        tree = TrashObjectSnapshot(
            source=path,
            root_state=state,
            tree_digest="0" * 64,
            object_count=1,
            total_size_bytes=0,
            largest_item_bytes=0,
            hidden_count=0,
            system_count=0,
            reparse_count=0,
            offline_count=0,
        )
        return CleanupMaterialSnapshot(
            tree=tree,
            file_count=1,
            directory_count=0,
            recently_modified_count=0,
            hidden_count=0,
            reparse_count=0,
            sensitive_signal_count=0,
            classification_digest="0" * 64,
        )

    @staticmethod
    def _placeholder_path_safety() -> CleanupPathSafetyDecision:
        return CleanupPathSafetyDecision(
            safe=False,
            current_user_scope=False,
            exact_known_root=False,
            other_user=False,
            shared_location=False,
            sensitive_path=False,
            reparse_detected=False,
            network_or_unsupported_volume=False,
            reason_codes=("non-direct-category",),
        )

    @staticmethod
    def _placeholder_activity() -> CleanupActivityDecision:
        return CleanupActivityDecision(
            recently_modified=False,
            delete_access_available=False,
            active_installer_detected=False,
            blocked=True,
            reason_codes=("non-direct-category",),
        )
