"""Provider-neutral models for controlled Windows process actions."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, model_validator

from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel


class ProcessActionType(StrEnum):
    """The only two process mutations available in Stage 4A."""

    REQUEST_GRACEFUL_EXIT = "REQUEST_GRACEFUL_EXIT"
    FORCE_TERMINATE = "FORCE_TERMINATE"


class ProcessTargetQueryType(StrEnum):
    """Finite local target-resolution modes."""

    PID = "PID"
    NAME = "NAME"
    SELECTED_PROCESS = "SELECTED_PROCESS"


class ProcessSafetyClass(StrEnum):
    """Deterministic process categories used by the safety policy."""

    USER_APPLICATION = "USER_APPLICATION"
    USER_BACKGROUND_PROCESS = "USER_BACKGROUND_PROCESS"
    SYSTEM_PROCESS = "SYSTEM_PROCESS"
    SERVICE_PROCESS = "SERVICE_PROCESS"
    SECURITY_PROCESS = "SECURITY_PROCESS"
    AGENT_PROCESS = "AGENT_PROCESS"
    UNKNOWN_SENSITIVE = "UNKNOWN_SENSITIVE"


class ProcessSafetyDecision(StrEnum):
    """A final allow/block outcome; no model can override it."""

    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


class ProcessActionErrorCode(StrEnum):
    """Stable user-facing and audit error identifiers."""

    BLOCKED_SYSTEM_PROCESS = "BLOCKED_SYSTEM_PROCESS"
    BLOCKED_SECURITY_PROCESS = "BLOCKED_SECURITY_PROCESS"
    BLOCKED_SERVICE_PROCESS = "BLOCKED_SERVICE_PROCESS"
    BLOCKED_DIFFERENT_USER = "BLOCKED_DIFFERENT_USER"
    BLOCKED_DIFFERENT_SESSION = "BLOCKED_DIFFERENT_SESSION"
    BLOCKED_AGENT_PROCESS = "BLOCKED_AGENT_PROCESS"
    BLOCKED_UNKNOWN_SENSITIVE = "BLOCKED_UNKNOWN_SENSITIVE"
    PROCESS_IDENTITY_CHANGED = "PROCESS_IDENTITY_CHANGED"
    PROCESS_ALREADY_EXITED = "PROCESS_ALREADY_EXITED"
    PROCESS_ACCESS_DENIED = "PROCESS_ACCESS_DENIED"
    UNSUPPORTED_GRACEFUL_EXIT = "UNSUPPORTED_GRACEFUL_EXIT"
    GRACEFUL_EXIT_TIMEOUT = "GRACEFUL_EXIT_TIMEOUT"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    CONFIRMATION_EXPIRED = "CONFIRMATION_EXPIRED"
    CONFIRMATION_REPLAYED = "CONFIRMATION_REPLAYED"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    TARGET_GROUP_CHANGED = "TARGET_GROUP_CHANGED"
    PLATFORM_ERROR = "PLATFORM_ERROR"
    AUDIT_UNAVAILABLE = "AUDIT_UNAVAILABLE"


class ProcessActionState(StrEnum):
    """Persistent lifecycle for one process action transaction."""

    PLANNED = "PLANNED"
    PREVIEWED = "PREVIEWED"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    AWAITING_RUNTIME_CONFIRMATION = "AWAITING_RUNTIME_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    VALIDATING = "VALIDATING"
    REQUESTING_GRACEFUL_EXIT = "REQUESTING_GRACEFUL_EXIT"
    WAITING_FOR_EXIT = "WAITING_FOR_EXIT"
    GRACEFUL_COMPLETED = "GRACEFUL_COMPLETED"
    GRACEFUL_TIMEOUT = "GRACEFUL_TIMEOUT"
    FORCE_TERMINATING = "FORCE_TERMINATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    ALREADY_EXITED = "ALREADY_EXITED"
    INTERRUPTED = "INTERRUPTED"
    UNKNOWN = "UNKNOWN"


class ProcessMemberResultState(StrEnum):
    """Verified outcome for one identity in an application target."""

    EXITED = "EXITED"
    ALREADY_EXITED = "ALREADY_EXITED"
    STILL_RUNNING = "STILL_RUNNING"
    IDENTITY_CHANGED = "IDENTITY_CHANGED"
    ACCESS_DENIED = "ACCESS_DENIED"
    UNSUPPORTED = "UNSUPPORTED"
    CANCELLED_WAITING = "CANCELLED_WAITING"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    FAILED = "FAILED"


class ProcessIdentity(FrozenModel):
    """Stable-enough process identity used to defend against PID reuse."""

    pid: int = Field(ge=1)
    process_name: str = Field(min_length=1, max_length=500)
    create_time: datetime
    executable_path: Path
    owner_sid: str = Field(pattern=r"^S-\d(?:-\d+)+$", max_length=300)
    username: str | None = Field(default=None, max_length=500)
    session_id: int = Field(ge=0)
    parent_pid: int | None = Field(default=None, ge=0)

    def canonical_digest(self) -> str:
        """Hash the fields that must still match immediately before execution."""
        payload = {
            "pid": self.pid,
            "process_name": self.process_name.casefold(),
            "create_time": self.create_time.astimezone(UTC).isoformat(),
            "executable_path": os.path.normcase(str(self.executable_path)),
            "owner_sid": self.owner_sid,
            "session_id": self.session_id,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


class ProcessObservation(FrozenModel):
    """Live local metadata used for resolution, Preview, and safety classification."""

    identity: ProcessIdentity
    cpu_percent: float = Field(default=0.0, ge=0)
    memory_rss_bytes: int = Field(default=0, ge=0)
    window_count: int = Field(default=0, ge=0)
    visible_window_count: int = Field(default=0, ge=0)
    service_names: tuple[str, ...] = ()
    is_critical: bool
    protection_level: int

    @property
    def graceful_supported(self) -> bool:
        """Return whether the available adapter can request a window-based exit."""
        return self.window_count > 0


class ProcessTargetQuery(FrozenModel):
    """A local semantic query; it never grants authority to a model-provided PID."""

    query_type: ProcessTargetQueryType
    text: str | None = Field(default=None, min_length=1, max_length=500)
    pid: int | None = Field(default=None, ge=1)
    include_application_group: bool = True

    @model_validator(mode="after")
    def require_one_query_value(self) -> Self:
        """Require exactly the value appropriate for the selected query type."""
        if self.query_type is ProcessTargetQueryType.NAME:
            if self.text is None or self.pid is not None:
                raise ValueError("Name queries require text and cannot include a PID")
        elif self.pid is None or self.text is not None:
            raise ValueError("PID and selected-process queries require only a PID")
        return self


class ResolvedProcessTarget(FrozenModel):
    """A concrete current application or single-process target."""

    target_id: UUID = Field(default_factory=uuid4)
    display_name: str = Field(min_length=1, max_length=500)
    application_group_key: str = Field(min_length=64, max_length=64)
    members: tuple[ProcessObservation, ...] = Field(min_length=1, max_length=20)

    def identity_set_digest(self) -> str:
        """Bind Preview and confirmation to the exact sorted member identities."""
        value = "\n".join(sorted(member.identity.canonical_digest() for member in self.members))
        return hashlib.sha256(value.encode()).hexdigest()


class ProcessSafetyAssessment(FrozenModel):
    """Deterministic policy decision for one current process identity."""

    identity_digest: str = Field(min_length=64, max_length=64)
    safety_class: ProcessSafetyClass
    decision: ProcessSafetyDecision
    reason_codes: tuple[ProcessActionErrorCode, ...]
    explanation: str = Field(min_length=1, max_length=1_000)


class ProcessActionPlan(FrozenModel):
    """Immutable action plan whose targets were resolved from current local state."""

    plan_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID = Field(default_factory=uuid4)
    operation_id: UUID = Field(default_factory=uuid4)
    parent_transaction_id: UUID | None = None
    plan_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_goal: str = Field(min_length=1, max_length=2_000)
    summary: str = Field(min_length=1, max_length=500)
    action: ProcessActionType
    target_query: ProcessTargetQuery
    targets: tuple[ResolvedProcessTarget, ...] = Field(min_length=1, max_length=5)
    risk_level: RiskLevel
    requires_plan_confirmation: bool = True
    requires_runtime_confirmation: bool = True
    rollback_level: RollbackLevel = RollbackLevel.NONE
    estimated_processes_affected: int = Field(ge=1, le=20)

    @model_validator(mode="after")
    def validate_safety_contract(self) -> Self:
        """Reject action/risk/confirmation combinations outside the Stage 4A contract."""
        expected = (
            RiskLevel.R2
            if self.action is ProcessActionType.REQUEST_GRACEFUL_EXIT
            else RiskLevel.R2_HIGH_IMPACT
        )
        if self.risk_level is not expected:
            raise ValueError("Process action risk does not match its deterministic action")
        if not self.requires_plan_confirmation or not self.requires_runtime_confirmation:
            raise ValueError("Every process action requires plan and runtime confirmation")
        if self.rollback_level is not RollbackLevel.NONE:
            raise ValueError("Process termination cannot truthfully offer Undo")
        member_count = sum(len(target.members) for target in self.targets)
        if member_count != self.estimated_processes_affected:
            raise ValueError("Estimated process impact must match the exact target set")
        return self

    def canonical_digest(self) -> str:
        """Hash every execution-relevant field for confirmation binding."""
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def target_set_digest(self) -> str:
        """Return a stable digest for every planned process identity."""
        values = [
            member.identity.canonical_digest()
            for target in self.targets
            for member in target.members
        ]
        return hashlib.sha256("\n".join(sorted(values)).encode()).hexdigest()


class ProcessActionPreview(FrozenModel):
    """Read-only live Preview of exact objects, classifications, and impact."""

    preview_id: UUID = Field(default_factory=uuid4)
    transaction_id: UUID
    plan_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    action: ProcessActionType
    targets: tuple[ResolvedProcessTarget, ...] = Field(min_length=1, max_length=5)
    assessments: tuple[ProcessSafetyAssessment, ...] = Field(min_length=1, max_length=20)
    target_set_digest: str = Field(min_length=64, max_length=64)
    application_count: int = Field(ge=1, le=5)
    process_count: int = Field(ge=1, le=20)
    total_memory_rss_bytes: int = Field(ge=0)
    total_cpu_percent: float = Field(ge=0)
    graceful_supported_count: int = Field(ge=0)
    rollback_level: RollbackLevel = RollbackLevel.NONE

    @property
    def executable(self) -> bool:
        """Return true only when every concrete member is deterministically allowed."""
        return len(self.assessments) == self.process_count and all(
            item.decision is ProcessSafetyDecision.ALLOW for item in self.assessments
        )

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        """Ensure displayed totals describe the exact target collection."""
        members = tuple(member for target in self.targets for member in target.members)
        if self.application_count != len(self.targets) or self.process_count != len(members):
            raise ValueError("Process Preview counts do not match its concrete targets")
        if self.target_set_digest != _identity_set_digest(members):
            raise ValueError("Process Preview identity set digest is invalid")
        return self

    def canonical_digest(self) -> str:
        """Hash the complete live Preview for confirmation binding."""
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


class ProcessActionRequest(FrozenModel):
    """Strict registered-tool input containing only prevalidated identities."""

    action: ProcessActionType
    identities: tuple[ProcessIdentity, ...] = Field(min_length=1, max_length=20)
    target_set_digest: str = Field(min_length=64, max_length=64)
    timeout_seconds: float = Field(ge=1.0, le=30.0)

    @model_validator(mode="after")
    def validate_digest(self) -> Self:
        """Reject an argument payload whose member set changed after authorization."""
        observations = tuple(
            ProcessObservation(
                identity=value,
                is_critical=False,
                protection_level=0xFFFFFFFE,
            )
            for value in self.identities
        )
        if self.target_set_digest != _identity_set_digest(observations):
            raise ValueError("Process action target-set digest is invalid")
        return self


class ProcessMemberResult(FrozenModel):
    """Outcome and verification evidence for one planned identity."""

    identity_digest: str = Field(min_length=64, max_length=64)
    pid: int = Field(ge=1)
    state: ProcessMemberResultState
    windows_notified: int = Field(default=0, ge=0)
    platform_error_code: int | None = Field(default=None, ge=0)
    message: str = Field(min_length=1, max_length=1_000)


class ProcessActionToolResult(FrozenModel):
    """Typed platform result returned by either registered process action tool."""

    action: ProcessActionType
    members: tuple[ProcessMemberResult, ...] = Field(min_length=1, max_length=20)
    started_at: datetime
    completed_at: datetime

    @property
    def all_exited(self) -> bool:
        """Return true when every original target is gone or had already exited."""
        return all(
            item.state
            in {
                ProcessMemberResultState.EXITED,
                ProcessMemberResultState.ALREADY_EXITED,
            }
            for item in self.members
        )


class ProcessActionTransaction(FrozenModel):
    """Durable summary of a non-reversible process action."""

    transaction_id: UUID
    operation_id: UUID
    plan_id: UUID
    preview_id: UUID
    parent_transaction_id: UUID | None = None
    action: ProcessActionType
    state: ProcessActionState
    plan_digest: str = Field(min_length=64, max_length=64)
    preview_digest: str = Field(min_length=64, max_length=64)
    target_set_digest: str = Field(min_length=64, max_length=64)
    process_count: int = Field(ge=1, le=20)
    plan_confirmation_id: UUID | None = None
    runtime_confirmation_id: UUID | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error_code: ProcessActionErrorCode | None = None
    error_message: str | None = Field(default=None, max_length=2_000)
    result: dict[str, JsonValue] | None = None


def _identity_set_digest(observations: tuple[ProcessObservation, ...]) -> str:
    values = sorted(item.identity.canonical_digest() for item in observations)
    return hashlib.sha256("\n".join(values).encode()).hexdigest()
