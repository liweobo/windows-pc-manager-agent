"""Narrow registered process-action tools; no shell or generic kill primitive exists."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from pydantic import BaseModel

from pc_manager_agent.domain.process_actions import (
    ProcessActionRequest,
    ProcessActionToolResult,
    ProcessActionType,
    ProcessIdentity,
    ProcessMemberResult,
    ProcessMemberResultState,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.platform_support.processes import ProcessManagementPlatform
from pc_manager_agent.tools.manifest import CancellationToken, ToolManifest


class RequestProcessExitTool:
    """Request WM_CLOSE for exact current-user process identities and verify exit."""

    def __init__(self, platform: ProcessManagementPlatform) -> None:
        self._platform = platform
        self._manifest = _manifest(
            "system.process.request_exit",
            "Request graceful WM_CLOSE exit for exact approved ordinary-user processes",
            RiskLevel.R2,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the fixed R2 graceful-exit manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Execute only the typed graceful action and stop future members on cancellation."""
        typed = ProcessActionRequest.model_validate(request)
        if typed.action is not ProcessActionType.REQUEST_GRACEFUL_EXIT:
            raise ValueError("Graceful tool accepts only REQUEST_GRACEFUL_EXIT")
        started = datetime.now(UTC)

        def request_member(identity: ProcessIdentity) -> ProcessMemberResult:
            current = identity
            if cancellation.is_cancelled:
                return ProcessMemberResult(
                    identity_digest=current.canonical_digest(),
                    pid=current.pid,
                    state=ProcessMemberResultState.CANCELLED_WAITING,
                    message="Cancelled before this member received WM_CLOSE",
                )
            return self._platform.request_graceful_exit(
                current,
                typed.timeout_seconds,
                cancellation,
            )

        # Application groups are notified concurrently so the configured timeout is
        # a group-level wait bound instead of being multiplied by helper processes.
        with ThreadPoolExecutor(
            max_workers=min(len(typed.identities), 20),
            thread_name_prefix="process-exit",
        ) as executor:
            results: list[ProcessMemberResult] = list(
                executor.map(request_member, typed.identities)
            )
        return ProcessActionToolResult(
            action=typed.action,
            members=tuple(results),
            started_at=started,
            completed_at=datetime.now(UTC),
        )


class ForceTerminateProcessTool:
    """Force-terminate exact separately confirmed ordinary-user process identities."""

    def __init__(self, platform: ProcessManagementPlatform) -> None:
        self._platform = platform
        self._manifest = _manifest(
            "system.process.force_terminate",
            "Force terminate exact separately approved ordinary-user processes",
            RiskLevel.R2_HIGH_IMPACT,
        )

    @property
    def manifest(self) -> ToolManifest:
        """Return the fixed R2 high-impact force-termination manifest."""
        return self._manifest

    def execute(self, request: BaseModel, cancellation: CancellationToken) -> BaseModel:
        """Execute the typed force action without automatic privilege escalation."""
        typed = ProcessActionRequest.model_validate(request)
        if typed.action is not ProcessActionType.FORCE_TERMINATE:
            raise ValueError("Force tool accepts only FORCE_TERMINATE")
        started = datetime.now(UTC)
        results: list[ProcessMemberResult] = []
        for index, identity in enumerate(typed.identities):
            if cancellation.is_cancelled:
                results.append(
                    ProcessMemberResult(
                        identity_digest=identity.canonical_digest(),
                        pid=identity.pid,
                        state=ProcessMemberResultState.CANCELLED_WAITING,
                        message="Cancelled before this member was force terminated",
                    )
                )
                continue
            result = self._platform.force_terminate(identity, typed.timeout_seconds)
            results.append(result)
            if result.state is ProcessMemberResultState.FAILED:
                results.extend(
                    ProcessMemberResult(
                        identity_digest=pending.canonical_digest(),
                        pid=pending.pid,
                        state=ProcessMemberResultState.NOT_ATTEMPTED,
                        message="Not attempted because an earlier force operation failed",
                    )
                    for pending in typed.identities[index + 1 :]
                )
                break
        return ProcessActionToolResult(
            action=typed.action,
            members=tuple(results),
            started_at=started,
            completed_at=datetime.now(UTC),
        )


def _manifest(name: str, description: str, risk: RiskLevel) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=description,
        input_model=ProcessActionRequest,
        output_model=ProcessActionToolResult,
        risk_level=risk,
        required_permissions=("ordinary-user",),
        read_only=False,
        idempotent=False,
        supports_cancellation=True,
        rollback_level=RollbackLevel.NONE,
        preconditions=(
            "deterministic safety approval",
            "two consumed same-action confirmations",
            "execution-time handle identity match",
        ),
        postconditions=("original process identity is verified exited or truthful failure",),
        timeout_seconds=35.0,
        max_batch_size=20,
        audit_fields=(
            "transaction_id",
            "action",
            "identity_digest",
            "result_state",
        ),
        supported_platforms=("windows",),
        requires_confirmation=True,
        requires_runtime_confirmation=True,
        supports_preview=True,
        irreversible=True,
    )
