"""Deterministic Stage 2B plan compilation from explicit local selections."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from pc_manager_agent.authorization.service import AuthorizedPathService
from pc_manager_agent.domain.file_operations import FileObjectKind, OperationType
from pc_manager_agent.domain.trash import TrashPlan, TrashPlanItem
from pc_manager_agent.platform_support.base import FileOperationPlatform
from pc_manager_agent.safety.path_policy import path_is_within
from pc_manager_agent.safety.trash_policy import TrashPathPolicy


class TrashIntentDecision(StrEnum):
    """Deterministic routing result that never contains paths or executable commands."""

    NONE = "NONE"
    RECYCLE_BIN = "RECYCLE_BIN"
    PROHIBITED_PERMANENT_DELETE = "PROHIBITED_PERMANENT_DELETE"


def classify_trash_intent(text: str) -> TrashIntentDecision:
    """Recognize recycle intent and refuse explicit permanent-delete wording locally."""
    normalized = text.strip().casefold()
    permanent_terms = (
        "永久删除",
        "彻底删除",
        "跳过回收站",
        "清空回收站",
        "permanently delete",
        "permanent delete",
        "delete forever",
        "empty recycle bin",
        "bypass recycle bin",
    )
    if any(term in normalized for term in permanent_terms):
        return TrashIntentDecision.PROHIBITED_PERMANENT_DELETE
    recycle_terms = (
        "回收站",
        "移入回收",
        "删除",
        "recycle bin",
        "move to recycle bin",
        "send to recycle bin",
        "recycle selected",
        "delete",
    )
    if any(term in normalized for term in recycle_terms):
        return TrashIntentDecision.RECYCLE_BIN
    return TrashIntentDecision.NONE


class TrashPlanCompiler:
    """Compile only paths explicitly selected by the user; no provider chooses objects."""

    def __init__(
        self,
        authorized_paths: AuthorizedPathService,
        policy: TrashPathPolicy,
        identity_platform: FileOperationPlatform,
        *,
        max_selected: int = 100,
    ) -> None:
        if max_selected <= 0:
            raise ValueError("Trash selected-object limit must be positive")
        self._authorized_paths = authorized_paths
        self._policy = policy
        self._identity = identity_platform
        self._max_selected = max_selected

    def compile(
        self,
        user_goal: str,
        selected_paths: Iterable[Path],
        authorized_root_ids: tuple[UUID, ...],
    ) -> TrashPlan:
        """Resolve identities and produce an ordered R2 plan without executing writes."""
        goal = user_goal.strip()
        if not goal:
            raise ValueError("A user goal is required")
        paths = tuple(selected_paths)
        if not paths:
            raise ValueError("Select at least one file or directory")
        if len(paths) > self._max_selected:
            raise ValueError(f"Select at most {self._max_selected} objects")
        if len(set(authorized_root_ids)) != len(authorized_root_ids):
            raise ValueError("Authorized root IDs must be unique")
        records = self._authorized_paths.resolve_authorized(authorized_root_ids)
        allowed_ids = {record.path_id for record in records}
        if allowed_ids != set(authorized_root_ids):
            raise PermissionError("Trash plan references an unknown authorized root")
        seen: set[str] = set()
        items = []
        for sequence, raw in enumerate(paths):
            source = self._policy.validate_source(raw)
            if not any(path_is_within(source, record.path) for record in records):
                raise PermissionError("Selected path is outside the plan's authorized roots")
            key = str(source).casefold()
            if key in seen:
                raise ValueError(f"Selected path appears more than once: {source}")
            seen.add(key)
            state = self._identity.inspect(source)
            operation_type = (
                OperationType.RECYCLE_FILE
                if state.kind is FileObjectKind.FILE
                else OperationType.RECYCLE_DIRECTORY
            )
            items.append(
                TrashPlanItem(
                    sequence=sequence,
                    operation_type=operation_type,
                    source=source,
                    expected_source_state=state,
                )
            )
        return TrashPlan(
            summary=f"Move {len(items)} explicitly selected object(s) to Windows Recycle Bin",
            user_goal=goal,
            authorized_root_ids=authorized_root_ids,
            items=tuple(items),
        )
