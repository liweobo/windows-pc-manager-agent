"""Read-only identity and directory-tree Preview for Stage 2B."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from uuid import UUID, uuid4

from pc_manager_agent.domain.file_operations import FileObjectKind
from pc_manager_agent.domain.trash import (
    TrashImpactLevel,
    TrashObjectSnapshot,
    TrashPlan,
    TrashPreview,
    TrashPreviewIssue,
    TrashPreviewItem,
    TrashPreviewStatus,
)
from pc_manager_agent.platform_support.base import FileOperationPlatform, RecycleBinPlatform
from pc_manager_agent.safety.path_policy import PathSecurityError
from pc_manager_agent.safety.trash_policy import TrashPathPolicy

_HIDDEN = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
_SYSTEM = getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_OFFLINE = getattr(stat, "FILE_ATTRIBUTE_OFFLINE", 0x1000)


class TrashPreviewEngine:
    """Generate bounded R2 Previews without invoking the Recycle Bin adapter."""

    def __init__(
        self,
        path_policy: TrashPathPolicy,
        identity_platform: FileOperationPlatform,
        recycle_platform: RecycleBinPlatform,
        *,
        max_selected: int = 100,
        max_contained_objects: int = 10_000,
        max_total_bytes: int = 50 * 1024**3,
        high_impact_objects: int = 100,
        high_impact_bytes: int = 10 * 1024**3,
    ) -> None:
        if (
            min(
                max_selected,
                max_contained_objects,
                max_total_bytes,
                high_impact_objects,
                high_impact_bytes,
            )
            <= 0
        ):
            raise ValueError("Trash Preview limits must be positive")
        self._policy = path_policy
        self._identity = identity_platform
        self._recycle = recycle_platform
        self._max_selected = max_selected
        self._max_contained_objects = max_contained_objects
        self._max_total_bytes = max_total_bytes
        self._high_impact_objects = high_impact_objects
        self._high_impact_bytes = high_impact_bytes

    def generate(self, plan: TrashPlan, transaction_id: UUID | None = None) -> TrashPreview:
        """Inspect all selected objects and retain blocked rows for an exact user Preview."""
        items: list[TrashPreviewItem] = []
        for planned in plan.items:
            try:
                source = self._policy.validate_source(planned.source)
                current = self._identity.inspect(source)
                if not current.unchanged_since(planned.expected_source_state):
                    raise PathSecurityError("Selected object changed after plan compilation")
                snapshot = self.snapshot(source, current.kind)
                capability = self._recycle.capability(source)
                if not capability.available:
                    raise PathSecurityError(capability.reason or "Recycle Bin is unavailable")
                item = TrashPreviewItem(
                    operation_id=planned.operation_id,
                    sequence=planned.sequence,
                    source=source,
                    status=TrashPreviewStatus.READY,
                    snapshot=snapshot,
                    capability=capability,
                )
            except (OSError, PathSecurityError, ValueError) as exc:
                item = TrashPreviewItem(
                    operation_id=planned.operation_id,
                    sequence=planned.sequence,
                    source=planned.source,
                    status=TrashPreviewStatus.BLOCKED,
                    issues=(TrashPreviewIssue(code="SOURCE_BLOCKED", message=str(exc)),),
                )
            items.append(item)

        snapshots = tuple(item.snapshot for item in items if item.snapshot is not None)
        contained = sum(item.object_count for item in snapshots)
        total_bytes = sum(item.total_size_bytes for item in snapshots)
        limit_reason = None
        if len(items) > self._max_selected:
            limit_reason = f"Selected-object limit exceeded: {len(items)} > {self._max_selected}"
        elif contained > self._max_contained_objects:
            limit_reason = (
                f"Contained-object limit exceeded: {contained} > {self._max_contained_objects}"
            )
        elif total_bytes > self._max_total_bytes:
            limit_reason = f"Byte limit exceeded: {total_bytes} > {self._max_total_bytes}"
        if limit_reason is not None:
            items = [
                item.model_copy(
                    update={
                        "status": TrashPreviewStatus.BLOCKED,
                        "issues": (
                            *item.issues,
                            TrashPreviewIssue(code="BATCH_LIMIT_EXCEEDED", message=limit_reason),
                        ),
                    }
                )
                if item.status is TrashPreviewStatus.READY
                else item
                for item in items
            ]
        object_digest = self._object_set_digest(items)
        return TrashPreview(
            transaction_id=transaction_id or uuid4(),
            plan_id=plan.plan_id,
            plan_digest=plan.canonical_digest(),
            items=tuple(items),
            object_set_digest=object_digest,
            selected_count=len(items),
            ready_count=sum(item.status is TrashPreviewStatus.READY for item in items),
            blocked_count=sum(item.status is TrashPreviewStatus.BLOCKED for item in items),
            contained_object_count=contained,
            total_size_bytes=total_bytes,
            largest_item_bytes=max((item.largest_item_bytes for item in snapshots), default=0),
            impact_level=(
                TrashImpactLevel.HIGH
                if contained > self._high_impact_objects or total_bytes > self._high_impact_bytes
                else TrashImpactLevel.NORMAL
            ),
        )

    def require_unchanged(self, plan: TrashPlan, preview: TrashPreview) -> TrashPreview:
        """Regenerate immediately before runtime confirmation and reject any difference."""
        current = self.generate(plan, transaction_id=preview.transaction_id)
        # Preview IDs and times are expected to change, so compare the bound live object set.
        if (
            current.object_set_digest != preview.object_set_digest
            or current.ready_count != preview.ready_count
            or current.blocked_count != preview.blocked_count
            or current.total_size_bytes != preview.total_size_bytes
        ):
            raise PathSecurityError("Selected objects changed; generate and confirm a new Preview")
        return current

    def snapshot(self, source: Path, kind: FileObjectKind) -> TrashObjectSnapshot:
        """Capture a deterministic bounded object tree without following redirects."""
        entries = []
        root_state = self._identity.inspect(source)
        stack = [source]
        total_bytes = 0
        largest = 0
        hidden = system = reparse = offline = 0
        while stack:
            current_path = stack.pop()
            reason = self._policy.entry_rejection_reason(current_path)
            if reason is not None:
                raise PathSecurityError(reason)
            state = self._identity.inspect(current_path)
            relative = (
                "." if current_path == source else current_path.relative_to(source).as_posix()
            )
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
                )
            )
            if state.kind is FileObjectKind.FILE:
                total_bytes += state.size_bytes
                largest = max(largest, state.size_bytes)
            attributes = state.attributes
            hidden += int(bool(attributes & _HIDDEN))
            system += int(bool(attributes & _SYSTEM))
            reparse += int(bool(attributes & _REPARSE))
            offline += int(bool(attributes & _OFFLINE))
            if state.kind is FileObjectKind.DIRECTORY:
                with os.scandir(current_path) as children:
                    child_paths = sorted((Path(child.path) for child in children), reverse=True)
                stack.extend(child_paths)
            if len(entries) > self._max_contained_objects:
                raise PathSecurityError("Directory tree exceeds the contained-object limit")
            if total_bytes > self._max_total_bytes:
                raise PathSecurityError("Directory tree exceeds the byte limit")
        encoded = json.dumps(sorted(entries), ensure_ascii=False, separators=(",", ":"))
        return TrashObjectSnapshot(
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

    @staticmethod
    def _object_set_digest(items: list[TrashPreviewItem]) -> str:
        payload = [
            {
                "operation_id": str(item.operation_id),
                "source": str(item.source),
                "status": item.status.value,
                "snapshot": item.snapshot.canonical_digest() if item.snapshot else None,
                "capability": item.capability.model_dump(mode="json") if item.capability else None,
                "issues": [issue.model_dump(mode="json") for issue in item.issues],
            }
            for item in items
        ]
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
