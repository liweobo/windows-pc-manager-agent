"""Immutable models for safe, previewed Stage 2A file operations."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel


class OperationType(StrEnum):
    """Allow-listed Stage 2A filesystem mutation types."""

    CREATE_DIRECTORY = "CREATE_DIRECTORY"
    MOVE_FILE = "MOVE_FILE"
    MOVE_DIRECTORY = "MOVE_DIRECTORY"
    RENAME_FILE = "RENAME_FILE"
    RENAME_DIRECTORY = "RENAME_DIRECTORY"


class FileObjectKind(StrEnum):
    """Kinds of filesystem objects Stage 2A can safely identify."""

    FILE = "FILE"
    DIRECTORY = "DIRECTORY"


class RenameRuleType(StrEnum):
    """Finite rename transformations accepted from UI or provider output."""

    PREFIX = "prefix"
    SUFFIX = "suffix"
    SEQUENCE = "sequence"
    LOWERCASE = "lowercase"
    UPPERCASE = "uppercase"
    REPLACE_TEXT = "replace_text"
    DATE_PREFIX = "date_prefix"


class OrganizationGroup(StrEnum):
    """Finite metadata fields that may determine organization folders."""

    MODIFIED_YEAR = "modified_year"


class ConflictPolicy(StrEnum):
    """Conflict handling policies supported in Stage 2A."""

    STOP = "STOP"


class PreviewItemStatus(StrEnum):
    """Whether a previewed item may be executed without changing the plan."""

    READY = "READY"
    CONFLICT = "CONFLICT"
    BLOCKED = "BLOCKED"


class FileState(FrozenModel):
    """Observed filesystem identity and metadata used for TOCTOU checks."""

    path: Path
    kind: FileObjectKind
    volume_serial: int = Field(ge=0)
    file_id: str = Field(min_length=1, max_length=64)
    size_bytes: int = Field(ge=0)
    created_ns: int = Field(ge=0)
    modified_ns: int = Field(ge=0)
    attributes: int = Field(ge=0)

    def identity_matches(self, other: FileState) -> bool:
        """Return whether two observations refer to the same filesystem object."""
        return (
            self.kind is other.kind
            and self.volume_serial == other.volume_serial
            and self.file_id.casefold() == other.file_id.casefold()
        )

    def unchanged_since(self, earlier: FileState) -> bool:
        """Return whether identity and mutation-relevant metadata are unchanged."""
        return (
            self.identity_matches(earlier)
            and self.size_bytes == earlier.size_bytes
            and self.modified_ns == earlier.modified_ns
            and self.created_ns == earlier.created_ns
            and self.attributes == earlier.attributes
        )


class RenameRule(FrozenModel):
    """One deterministic rename rule; arbitrary code and regular expressions are absent."""

    rule_type: RenameRuleType
    value: str | None = Field(default=None, max_length=120)
    replacement: str | None = Field(default=None, max_length=120)
    start: int = Field(default=1, ge=0, le=999_999)
    width: int = Field(default=3, ge=1, le=8)

    @model_validator(mode="after")
    def validate_arguments(self) -> Self:
        """Require exactly the values needed by the selected finite rule."""
        needs_value = {
            RenameRuleType.PREFIX,
            RenameRuleType.SUFFIX,
            RenameRuleType.REPLACE_TEXT,
        }
        if self.rule_type in needs_value and not self.value:
            raise ValueError(f"{self.rule_type.value} requires a non-empty value")
        if self.rule_type is RenameRuleType.REPLACE_TEXT and self.replacement is None:
            raise ValueError("replace_text requires a replacement value")
        return self


class FileSelectionRule(FrozenModel):
    """Bounded deterministic selector used before concrete operations are compiled."""

    root_ids: tuple[UUID, ...]
    extensions: tuple[str, ...] = ()
    record_ids: tuple[int, ...] = ()
    recursive: bool = True

    @field_validator("extensions")
    @classmethod
    def normalize_extensions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Normalize extensions to lowercase dotted forms and reject wildcards."""
        normalized: list[str] = []
        for value in values:
            item = value.strip().lower()
            if not item or any(char in item for char in "*?[]/\\"):
                raise ValueError(f"Invalid file extension selector: {value!r}")
            dotted = item if item.startswith(".") else f".{item}"
            if len(dotted) > 32:
                raise ValueError("File extension selector is too long")
            normalized.append(dotted)
        return tuple(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def require_scope(self) -> Self:
        """Require explicit authorized roots and at least one finite selector."""
        if not self.root_ids:
            raise ValueError("At least one authorized root identifier is required")
        if not self.extensions and not self.record_ids:
            raise ValueError("At least one extension or selected record is required")
        return self


class FileOperationIntentDraft(FrozenModel):
    """Provider-neutral intent that cannot contain concrete executable commands."""

    selection: FileSelectionRule
    destination_root_id: UUID | None = None
    destination_subdirectory: tuple[str, ...] = ()
    group_by: OrganizationGroup | None = None
    rename_rule: RenameRule | None = None
    requested_operation: OperationType

    @model_validator(mode="after")
    def validate_intent_shape(self) -> Self:
        """Reject contradictory provider output before any local path is resolved."""
        if (
            self.requested_operation
            in {
                OperationType.MOVE_FILE,
                OperationType.MOVE_DIRECTORY,
                OperationType.CREATE_DIRECTORY,
            }
            and self.destination_root_id is None
        ):
            raise ValueError("A destination root identifier is required")
        if (
            self.requested_operation
            in {
                OperationType.RENAME_FILE,
                OperationType.RENAME_DIRECTORY,
            }
            and self.rename_rule is None
        ):
            raise ValueError("A structured rename rule is required")
        return self


class PlannedFileOperation(FrozenModel):
    """One concrete allow-listed mutation compiled by deterministic code."""

    operation_id: UUID = Field(default_factory=uuid4)
    sequence: int = Field(ge=0)
    operation_type: OperationType
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    source: Path | None = None
    destination: Path
    expected_source_state: FileState | None = None
    rollback_level: RollbackLevel = RollbackLevel.FULL
    internal_temporary_path: Path | None = None

    @model_validator(mode="after")
    def validate_operation_shape(self) -> Self:
        """Require source identity for mutations and forbid it for directory creation."""
        if self.operation_type is OperationType.CREATE_DIRECTORY:
            if self.source is not None or self.expected_source_state is not None:
                raise ValueError("CREATE_DIRECTORY cannot have a source")
            if self.tool_name != "file.mkdir":
                raise ValueError("CREATE_DIRECTORY must use file.mkdir")
        else:
            if self.source is None or self.expected_source_state is None:
                raise ValueError("Move and rename operations require source identity")
            expected_tool = (
                "file.rename"
                if self.operation_type
                in {OperationType.RENAME_FILE, OperationType.RENAME_DIRECTORY}
                else "file.move"
            )
            if self.tool_name != expected_tool:
                raise ValueError(f"{self.operation_type.value} must use {expected_tool}")
        return self


class FileOperationPlan(FrozenModel):
    """Complete R1 plan reviewed and previewed before any filesystem mutation."""

    plan_id: UUID = Field(default_factory=uuid4)
    plan_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: str = Field(min_length=1, max_length=500)
    user_goal: str = Field(min_length=1, max_length=2_000)
    authorized_root_ids: tuple[UUID, ...]
    operations: tuple[PlannedFileOperation, ...]
    risk_level: RiskLevel = RiskLevel.R1
    requires_confirmation: bool = True
    rollback_level: RollbackLevel = RollbackLevel.FULL
    conflict_policy: ConflictPolicy = ConflictPolicy.STOP

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        """Require deterministic order, unique identifiers, and the Stage 2A risk contract."""
        if not self.authorized_root_ids:
            raise ValueError("At least one authorized root is required")
        if not self.operations:
            raise ValueError("At least one file operation is required")
        if self.risk_level is not RiskLevel.R1 or not self.requires_confirmation:
            raise ValueError("Stage 2A operations must be confirmed R1 operations")
        if self.rollback_level is not RollbackLevel.FULL:
            raise ValueError("Stage 2A plans must target FULL rollback")
        identifiers = [item.operation_id for item in self.operations]
        sequences = [item.sequence for item in self.operations]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Operation identifiers must be unique")
        if sequences != list(range(len(sequences))):
            raise ValueError("Operation sequence must be contiguous and ordered")
        destinations = [str(item.destination).casefold() for item in self.operations]
        if len(destinations) != len(set(destinations)):
            raise ValueError("Two operations cannot reserve the same destination")
        return self

    def canonical_digest(self) -> str:
        """Hash all execution-relevant plan fields for Preview and confirmation binding."""
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class OperationPreviewIssue(FrozenModel):
    """Stable user-facing reason that a preview item cannot execute."""

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    message: str = Field(min_length=1, max_length=500)


class OperationPreviewItem(FrozenModel):
    """Live filesystem assessment for one planned operation."""

    operation_id: UUID
    sequence: int = Field(ge=0)
    status: PreviewItemStatus
    operation_type: OperationType
    source: Path | None
    destination: Path
    source_state: FileState | None = None
    destination_state: FileState | None = None
    issues: tuple[OperationPreviewIssue, ...] = ()
    rollback_level: RollbackLevel


class FileOperationPreview(FrozenModel):
    """Read-only snapshot that is independently bound to user confirmation."""

    preview_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID
    plan_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    items: tuple[OperationPreviewItem, ...]
    total_size_bytes: int = Field(ge=0)
    ready_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    full_rollback_count: int = Field(ge=0)
    risk_level: RiskLevel = RiskLevel.R1

    @model_validator(mode="after")
    def validate_totals(self) -> Self:
        """Ensure deterministic totals exactly match the item snapshot."""
        counts = {
            PreviewItemStatus.READY: self.ready_count,
            PreviewItemStatus.CONFLICT: self.conflict_count,
            PreviewItemStatus.BLOCKED: self.blocked_count,
        }
        for status, expected in counts.items():
            if sum(item.status is status for item in self.items) != expected:
                raise ValueError(f"Preview {status.value} total does not match items")
        if self.full_rollback_count != sum(
            item.status is PreviewItemStatus.READY and item.rollback_level is RollbackLevel.FULL
            for item in self.items
        ):
            raise ValueError("Preview FULL rollback total does not match items")
        return self

    def canonical_digest(self) -> str:
        """Hash the live Preview, including file identities and all blocked items."""
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
