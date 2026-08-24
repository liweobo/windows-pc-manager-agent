"""Fresh metadata-only revalidation for selected Stage 4D3 residual IDs."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections import Counter
from pathlib import Path

from pc_manager_agent.domain.file_operations import FileObjectKind
from pc_manager_agent.domain.residual_cleanup import (
    CleanupEligibilityDecision,
    FreshResidualCandidate,
    ResidualClassificationCount,
    ResidualCleanupAssessment,
    ResidualCleanupRequest,
    ResidualMaterialSnapshot,
    ResidualProtectionCount,
    ResidualRecoverabilityDecision,
)
from pc_manager_agent.domain.software_residuals import (
    ContextPathEvidence,
    OwnershipConfidence,
    ResidualAnalysisStatus,
    ResidualCandidate,
    ResidualClassification,
    ResidualObjectType,
    ResidualSource,
    UninstallContext,
    UserDataProtectionLevel,
)
from pc_manager_agent.domain.trash import TrashObjectSnapshot
from pc_manager_agent.persistence.software_residuals import SoftwareResidualRepository
from pc_manager_agent.platform_support.base import FileOperationPlatform, RecycleBinPlatform
from pc_manager_agent.safety.residual_classification import ResidualClassifier
from pc_manager_agent.safety.residual_cleanup_policy import (
    CleanupEligibilityPolicy,
    ResidualCleanupPathPolicy,
    ResidualCleanupPolicyError,
    ResidualRecentModificationPolicy,
)
from pc_manager_agent.safety.residual_ownership import ResidualOwnershipEvaluator
from pc_manager_agent.safety.user_data_protection import UserDataProtectionPolicy
from pc_manager_agent.tools.manifest import CancellationToken

_HIDDEN = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
_SYSTEM = getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_OFFLINE = getattr(stat, "FILE_ATTRIBUTE_OFFLINE", 0x1000)
_FORBIDDEN_CLASSES = frozenset(
    {
        ResidualClassification.TEMPORARY_DATA,
        ResidualClassification.CONFIGURATION,
        ResidualClassification.USER_DATA,
        ResidualClassification.DATABASE,
        ResidualClassification.PLUGIN_OR_EXTENSION,
        ResidualClassification.PACKAGE_USER_DATA,
        ResidualClassification.SERVICE_RELATED_ARTIFACT,
        ResidualClassification.APPLICATION_STATE,
        ResidualClassification.CRASH_DUMP,
        ResidualClassification.LICENSE_DATA,
        ResidualClassification.UNKNOWN,
    }
)
_FORBIDDEN_PROTECTION = frozenset(
    {
        UserDataProtectionLevel.PROTECTED,
        UserDataProtectionLevel.STRONGLY_PROTECTED,
        UserDataProtectionLevel.UNKNOWN,
    }
)


class ResidualCleanupRevalidationError(RuntimeError):
    """Raised when a fresh assessment cannot be completed without guessing."""


class FreshResidualRevalidator:
    """Resolve report IDs locally and build full fresh metadata snapshots."""

    def __init__(
        self,
        repository: SoftwareResidualRepository,
        path_policy: ResidualCleanupPathPolicy,
        classifier: ResidualClassifier,
        ownership: ResidualOwnershipEvaluator,
        protection: UserDataProtectionPolicy,
        eligibility: CleanupEligibilityPolicy,
        recent_activity: ResidualRecentModificationPolicy,
        identity_platform: FileOperationPlatform,
        recycle_platform: RecycleBinPlatform,
        *,
        max_selected: int,
        max_contained_objects: int,
        max_total_bytes: int,
    ) -> None:
        if min(max_selected, max_contained_objects, max_total_bytes) <= 0:
            raise ValueError("Residual cleanup revalidation limits must be positive")
        self._repository = repository
        self._path_policy = path_policy
        self._classifier = classifier
        self._ownership = ownership
        self._protection = protection
        self._eligibility = eligibility
        self._recent = recent_activity
        self._identity = identity_platform
        self._recycle = recycle_platform
        self._max_selected = max_selected
        self._max_objects = max_contained_objects
        self._max_bytes = max_total_bytes

    def assess(
        self,
        request: ResidualCleanupRequest,
        cancellation: CancellationToken | None = None,
    ) -> ResidualCleanupAssessment:
        """Freshly inspect only selected IDs and retain blocked rows for the UI."""
        if len(request.selected_residual_ids) > self._max_selected:
            raise ResidualCleanupRevalidationError(
                f"Select at most {self._max_selected} residual candidates"
            )
        token = cancellation or CancellationToken()
        report = self._repository.get_report(request.source_report_id)
        if report.status is not ResidualAnalysisStatus.COMPLETED:
            raise ResidualCleanupRevalidationError(
                "Only a complete Stage 4D3 report may be used as cleanup intent"
            )
        context = self._repository.get_context(report.context_id)
        by_id = {candidate.candidate_id: candidate for candidate in report.candidates}
        if set(request.selected_residual_ids) - set(by_id):
            raise ResidualCleanupRevalidationError(
                "Cleanup selection contains an unknown or cross-report candidate"
            )
        items: list[FreshResidualCandidate] = []
        total_objects = 0
        total_bytes = 0
        for candidate_id in request.selected_residual_ids:
            if token.cancellation_requested():
                raise ResidualCleanupRevalidationError("Residual cleanup revalidation cancelled")
            candidate = by_id[candidate_id]
            evidence = self._evidence_for(candidate, context)
            item = self._assess_candidate(candidate, context, evidence, token)
            if item.material is not None:
                total_objects += item.material.tree.object_count
                total_bytes += item.material.tree.total_size_bytes
            if total_objects > self._max_objects or total_bytes > self._max_bytes:
                raise ResidualCleanupRevalidationError(
                    "Selected residual batch exceeds the hard object or byte limit"
                )
            items.append(item)
        return ResidualCleanupAssessment(
            request=request,
            items=tuple(items),
            selected_count=len(items),
            eligible_count=sum(
                item.eligibility is CleanupEligibilityDecision.ELIGIBLE for item in items
            ),
            blocked_count=sum(
                item.eligibility is CleanupEligibilityDecision.BLOCKED for item in items
            ),
            manual_review_count=sum(
                item.eligibility is CleanupEligibilityDecision.MANUAL_REVIEW for item in items
            ),
        )

    def require_unchanged(
        self,
        expected: FreshResidualCandidate,
        cancellation: CancellationToken | None = None,
    ) -> FreshResidualCandidate:
        """Repeat one exact scan and reject any identity, policy, or material change."""
        request = ResidualCleanupRequest(
            source_report_id=expected.source_report_id,
            selected_residual_ids=(expected.source_candidate_id,),
        )
        current = self.assess(request, cancellation).items[0]
        if (
            current.eligibility is not CleanupEligibilityDecision.ELIGIBLE
            or current.invariant_digest() != expected.invariant_digest()
        ):
            raise ResidualCleanupRevalidationError(
                "Residual cleanup item changed after confirmation"
            )
        return current

    def _assess_candidate(
        self,
        candidate: ResidualCandidate,
        context: UninstallContext,
        evidence: ContextPathEvidence,
        cancellation: CancellationToken,
    ) -> FreshResidualCandidate:
        """Assess one exact candidate and fail closed into a blocked row."""
        try:
            if not self._old_identity_matches(candidate):
                return self._blocked(candidate, context, evidence, "stage4d3-identity-changed")
            path, path_safety = self._path_policy.validate_candidate(candidate, evidence)
            fresh_identity = self._identity.inspect(path)
            material = self._snapshot(path, evidence, cancellation)
            if not fresh_identity.unchanged_since(material.tree.root_state):
                return self._blocked(candidate, context, evidence, "root-changed-during-scan")
            classification, classification_reasons = self._classify(path, evidence)
            shared = self._path_policy.is_shared(path, evidence)
            ownership, ownership_evidence = self._ownership.evaluate(
                evidence,
                shared_location=shared,
            )
            protection, protection_reasons = self._protect(
                path,
                classification,
                evidence,
            )
            recent = self._recent.evaluate(context, material)
            capability = self._recycle.capability(path)
            recoverability = ResidualRecoverabilityDecision(
                capability=capability,
                reason_codes=(
                    ("windows-recycle-bin-manual-recovery",)
                    if capability.available
                    else ("recycle-bin-capability-unavailable",)
                ),
            )
            decision, reasons = self._eligibility.evaluate(
                context=context,
                evidence=evidence,
                classification=classification,
                ownership=ownership,
                protection=protection,
                material=material,
                path_safety=path_safety,
                recent_activity=recent,
                recycle_bin_available=capability.available,
            )
            return FreshResidualCandidate(
                source_report_id=candidate.report_id,
                source_candidate_id=candidate.candidate_id,
                context_id=context.context_id,
                uninstall_transaction_id=context.transaction_id,
                software_identity_digest=context.software_identity_digest,
                context_digest=context.canonical_digest(),
                path=path,
                scan_root=evidence.path,
                source=evidence.source,
                evidence_code=evidence.evidence_code,
                expected_old_identity_digest=candidate.identity.canonical_digest(),
                fresh_identity=fresh_identity,
                material=material,
                classification=classification,
                classification_reasons=classification_reasons,
                ownership_confidence=ownership,
                ownership_evidence=ownership_evidence,
                protection_level=protection,
                protection_reasons=protection_reasons,
                path_safety=path_safety,
                recent_activity=recent,
                recoverability=recoverability,
                eligibility=decision,
                eligibility_reason_codes=reasons,
            )
        except (OSError, PermissionError, ResidualCleanupPolicyError, ValueError) as exc:
            return self._blocked(
                candidate,
                context,
                evidence,
                f"fresh-revalidation-failed-{type(exc).__name__.casefold()}",
            )

    def _snapshot(
        self,
        source: Path,
        evidence: ContextPathEvidence,
        cancellation: CancellationToken,
    ) -> ResidualMaterialSnapshot:
        """Traverse a complete bounded tree without following or reading any entry."""
        root_state = self._identity.inspect(source)
        stack = [source]
        entries: list[tuple[object, ...]] = []
        total_bytes = 0
        largest = 0
        files = directories = 0
        hidden = system = reparse = offline = 0
        minimum_modified: int | None = None
        maximum_modified = 0
        classifications: Counter[ResidualClassification] = Counter()
        protections: Counter[UserDataProtectionLevel] = Counter()
        forbidden = 0
        while stack:
            if cancellation.cancellation_requested():
                raise ResidualCleanupRevalidationError("Residual tree scan cancelled")
            current_path = stack.pop()
            rejection = self._path_policy.entry_rejection_reason(current_path, source)
            if rejection is not None:
                raise ResidualCleanupPolicyError(rejection)
            state = self._identity.inspect(current_path)
            relative = (
                "." if current_path == source else current_path.relative_to(source).as_posix()
            )
            classification, _reasons = self._classify(current_path, evidence)
            protection, _protection_reasons = self._protect(
                current_path,
                classification,
                evidence,
            )
            classifications[classification] += 1
            protections[protection] += 1
            if classification in _FORBIDDEN_CLASSES or protection in _FORBIDDEN_PROTECTION:
                forbidden += 1
            entries.append(
                (
                    relative,
                    state.kind.value,
                    state.volume_serial,
                    state.file_id.casefold(),
                    state.size_bytes,
                    state.created_ns,
                    state.modified_ns,
                    state.attributes,
                    classification.value,
                    protection.value,
                )
            )
            minimum_modified = (
                state.modified_ns
                if minimum_modified is None
                else min(minimum_modified, state.modified_ns)
            )
            maximum_modified = max(maximum_modified, state.modified_ns)
            attributes = state.attributes
            hidden += int(bool(attributes & _HIDDEN))
            system += int(bool(attributes & _SYSTEM))
            reparse += int(bool(attributes & _REPARSE))
            offline += int(bool(attributes & _OFFLINE))
            if state.kind is FileObjectKind.FILE:
                files += 1
                total_bytes += state.size_bytes
                largest = max(largest, state.size_bytes)
            else:
                directories += 1
                with os.scandir(current_path) as children:
                    child_paths = sorted((Path(child.path) for child in children), reverse=True)
                stack.extend(child_paths)
            if len(entries) > self._max_objects:
                raise ResidualCleanupPolicyError("residual-tree-object-limit-exceeded")
            if total_bytes > self._max_bytes:
                raise ResidualCleanupPolicyError("residual-tree-byte-limit-exceeded")
        final_root = self._identity.inspect(source)
        if not final_root.unchanged_since(root_state):
            raise ResidualCleanupPolicyError("root-changed-during-material-snapshot")
        encoded = json.dumps(sorted(entries), ensure_ascii=False, separators=(",", ":"))
        tree = TrashObjectSnapshot(
            source=source,
            root_state=root_state,
            tree_digest=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            object_count=len(entries),
            total_size_bytes=total_bytes,
            largest_item_bytes=largest,
            hidden_count=hidden,
            system_count=system,
            reparse_count=reparse,
            offline_count=offline,
        )
        return ResidualMaterialSnapshot(
            tree=tree,
            file_count=files,
            directory_count=directories,
            minimum_modified_ns=minimum_modified or 0,
            maximum_modified_ns=maximum_modified,
            classification_counts=tuple(
                ResidualClassificationCount(classification=classification, count=count)
                for classification, count in sorted(
                    classifications.items(), key=lambda item: item[0].value
                )
            ),
            protection_counts=tuple(
                ResidualProtectionCount(protection=protection, count=count)
                for protection, count in sorted(protections.items(), key=lambda item: item[0].value)
            ),
            forbidden_descendant_count=forbidden,
        )

    def _classify(
        self,
        path: Path,
        evidence: ContextPathEvidence,
    ) -> tuple[ResidualClassification, tuple[str, ...]]:
        """Classify only the selected root and its relative descendants.

        Components above the exact uninstall-context root describe where the
        application lived, not what the selected residual contains.  Including
        them would, for example, misclassify every test or real application
        beneath a parent named ``Temp`` as temporary data.
        """
        root = Path(os.path.abspath(evidence.path))
        current = Path(os.path.abspath(path))
        relative = current.relative_to(root)
        logical_path = Path(root.name) / relative
        return self._classifier.classify(
            logical_path,
            evidence.source,
            evidence.expected_classification,
        )

    def _protect(
        self,
        path: Path,
        classification: ResidualClassification,
        evidence: ContextPathEvidence,
    ) -> tuple[UserDataProtectionLevel, tuple[str, ...]]:
        """Allow only an exact pre-uninstall shortcut to bypass library protection.

        Desktop and Start Menu paths are normally strongly protected.  Stage 4D4
        can nevertheless assess one exact shortcut when its path and target were
        captured before uninstall.  The shortcut remains CAUTION, and the later
        eligibility gate still requires HIGH ownership and an absent target.
        """
        if (
            classification is ResidualClassification.SHORTCUT
            and evidence.source is ResidualSource.SHORTCUT
            and evidence.related_target_path is not None
            and Path(os.path.abspath(path)) == Path(os.path.abspath(evidence.path))
        ):
            return UserDataProtectionLevel.CAUTION, (
                "exact-pre-uninstall-shortcut",
                "shortcut-target-evidence-present",
            )
        return self._protection.protect(path, classification)

    @staticmethod
    def _evidence_for(
        candidate: ResidualCandidate,
        context: UninstallContext,
    ) -> ContextPathEvidence:
        """Resolve one report root to the exact context evidence that created it."""
        report_root = os.path.normcase(os.path.abspath(os.fspath(candidate.scan_root)))
        matches = tuple(
            evidence
            for evidence in context.known_paths
            if evidence.source is candidate.source
            and os.path.normcase(os.path.abspath(os.fspath(evidence.path))) == report_root
        )
        if len(matches) != 1:
            raise ResidualCleanupRevalidationError(
                "Selected residual lacks one exact uninstall-context path"
            )
        return matches[0]

    @staticmethod
    def _old_identity_matches(candidate: ResidualCandidate) -> bool:
        """Compare the old lstat identity before acquiring a fresh Windows handle identity."""
        try:
            metadata = os.lstat(candidate.path)
        except OSError:
            return False
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        if attributes & _REPARSE or candidate.path.is_symlink():
            return False
        if stat.S_ISDIR(metadata.st_mode):
            object_type = ResidualObjectType.DIRECTORY
            size = 0
        elif stat.S_ISREG(metadata.st_mode):
            object_type = (
                ResidualObjectType.SHORTCUT
                if candidate.path.suffix.casefold() == ".lnk"
                else ResidualObjectType.FILE
            )
            size = metadata.st_size
        else:
            return False
        identity = candidate.identity
        return (
            Path(os.path.abspath(candidate.path)) == identity.normalized_path
            and metadata.st_dev == identity.device_id
            and metadata.st_ino == identity.file_id
            and object_type is identity.object_type
            and size == identity.size_bytes
            and metadata.st_mtime_ns == identity.modified_time_ns
        )

    @staticmethod
    def _blocked(
        candidate: ResidualCandidate,
        context: UninstallContext,
        evidence: ContextPathEvidence,
        reason: str,
    ) -> FreshResidualCandidate:
        """Represent an unscannable selection without borrowing stale report authority."""
        return FreshResidualCandidate(
            source_report_id=candidate.report_id,
            source_candidate_id=candidate.candidate_id,
            context_id=context.context_id,
            uninstall_transaction_id=context.transaction_id,
            software_identity_digest=context.software_identity_digest,
            context_digest=context.canonical_digest(),
            path=candidate.path,
            scan_root=evidence.path,
            source=evidence.source,
            evidence_code=evidence.evidence_code,
            expected_old_identity_digest=candidate.identity.canonical_digest(),
            classification=ResidualClassification.UNKNOWN,
            classification_reasons=("fresh-classification-unavailable",),
            ownership_confidence=OwnershipConfidence.UNKNOWN,
            ownership_evidence=(),
            protection_level=UserDataProtectionLevel.UNKNOWN,
            protection_reasons=("fresh-protection-unavailable",),
            eligibility=CleanupEligibilityDecision.BLOCKED,
            eligibility_reason_codes=(reason,),
        )
