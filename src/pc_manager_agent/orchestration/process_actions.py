"""End-to-end controlled process planning, confirmation, execution, and verification."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pc_manager_agent.audit.process_actions import ProcessActionAuditLogger
from pc_manager_agent.confirmation.process_actions import (
    ProcessActionConfirmation,
    ProcessActionConfirmationService,
)
from pc_manager_agent.domain.process_actions import (
    ProcessActionErrorCode,
    ProcessActionPlan,
    ProcessActionPreview,
    ProcessActionRequest,
    ProcessActionState,
    ProcessActionToolResult,
    ProcessActionType,
    ProcessMemberResult,
    ProcessMemberResultState,
    ProcessTargetQuery,
    ResolvedProcessTarget,
)
from pc_manager_agent.domain.process_errors import ProcessActionError
from pc_manager_agent.orchestration.process_action_planner import ProcessActionPlanCompiler
from pc_manager_agent.orchestration.process_target_resolver import ProcessTargetResolver
from pc_manager_agent.persistence.process_actions import ProcessActionRepository
from pc_manager_agent.safety.process_preview import ProcessPreviewEngine
from pc_manager_agent.safety.process_validator import (
    ProcessActionSafetyValidator,
    ProcessSafetyReview,
)
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


class ProcessActionService:
    """Coordinate the complete process action workflow without trusting UI state."""

    def __init__(
        self,
        compiler: ProcessActionPlanCompiler,
        resolver: ProcessTargetResolver,
        preview_engine: ProcessPreviewEngine,
        validator: ProcessActionSafetyValidator,
        confirmation: ProcessActionConfirmationService,
        repository: ProcessActionRepository,
        registry: ToolRegistry,
        audit: ProcessActionAuditLogger,
        *,
        graceful_timeout_seconds: float = 10.0,
        force_timeout_seconds: float = 10.0,
    ) -> None:
        self._compiler = compiler
        self._resolver = resolver
        self._preview_engine = preview_engine
        self._validator = validator
        self._confirmation = confirmation
        self._repository = repository
        self._registry = registry
        self._audit = audit
        self._graceful_timeout = graceful_timeout_seconds
        self._force_timeout = force_timeout_seconds

    def prepare_from_text(
        self,
        user_goal: str,
    ) -> tuple[ProcessActionPlan, ProcessActionPreview, ProcessSafetyReview]:
        """Resolve a fresh local target and persist its read-only Preview."""
        return self.prepare_plan(self._compiler.compile_from_text(user_goal))

    def prepare(
        self,
        user_goal: str,
        query: ProcessTargetQuery,
        action: ProcessActionType,
        *,
        parent_transaction_id: UUID | None = None,
    ) -> tuple[ProcessActionPlan, ProcessActionPreview, ProcessSafetyReview]:
        """Compile an explicit local query and build the same reviewed Preview."""
        plan = self._compiler.compile(
            user_goal,
            query,
            action,
            parent_transaction_id=parent_transaction_id,
        )
        return self.prepare_plan(plan)

    def prepare_plan(
        self,
        plan: ProcessActionPlan,
    ) -> tuple[ProcessActionPlan, ProcessActionPreview, ProcessSafetyReview]:
        """Build, independently review, persist, and audit one exact plan."""
        preview = self._preview_engine.build(plan)
        review = self._validator.review(plan, preview)
        arguments = self._arguments(plan)
        self._repository.create(plan, preview, _tool_name(plan.action), arguments)
        try:
            self._audit.previewed(plan, preview)
        except Exception:
            self._repository.transition(
                plan.transaction_id,
                ProcessActionState.FAILED,
                error_code=ProcessActionErrorCode.AUDIT_UNAVAILABLE,
                error_message="Mandatory process Preview audit could not be recorded",
            )
            raise
        self._repository.transition(
            plan.transaction_id,
            (
                ProcessActionState.AWAITING_CONFIRMATION
                if review.approved
                else ProcessActionState.BLOCKED
            ),
            error_code=(
                None if review.approved else ProcessActionErrorCode.BLOCKED_UNKNOWN_SENSITIVE
            ),
            error_message=None if review.approved else "; ".join(review.issues),
        )
        return plan, preview, review

    def request_plan_confirmation(
        self,
        plan: ProcessActionPlan,
        preview: ProcessActionPreview,
    ) -> ProcessActionConfirmation:
        """Issue and persist the first same-action confirmation request."""
        request = self._confirmation.request_plan(plan, preview)
        self._repository.record_confirmation(request)
        self._repository.bind_plan_confirmation(plan.transaction_id, request.confirmation_id)
        return request

    def resolve_plan_confirmation(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: ProcessActionPlan,
        preview: ProcessActionPreview,
    ) -> ProcessActionConfirmation:
        """Resolve and persist the first confirmation; rejection cancels the transaction."""
        resolved = self._confirmation.resolve_plan(confirmation_id, approved, plan, preview)
        self._repository.record_confirmation(resolved)
        self._audit.confirmation_resolved(plan, resolved)
        self._repository.transition(
            plan.transaction_id,
            (
                ProcessActionState.AWAITING_RUNTIME_CONFIRMATION
                if approved
                else ProcessActionState.CANCELLED
            ),
        )
        return resolved

    def request_runtime_confirmation(
        self,
        plan_confirmation_id: UUID,
        plan: ProcessActionPlan,
    ) -> tuple[ProcessActionPreview, ProcessActionConfirmation]:
        """Revalidate identities and policy before issuing the short-lived approval."""
        try:
            targets = self._revalidate_targets(plan)
        except ProcessActionError as exc:
            state = (
                ProcessActionState.ALREADY_EXITED
                if exc.code is ProcessActionErrorCode.PROCESS_ALREADY_EXITED
                else ProcessActionState.BLOCKED
            )
            self._repository.transition(
                plan.transaction_id,
                state,
                error_code=exc.code,
                error_message=str(exc),
            )
            self._audit.failed(
                plan,
                phase="runtime_confirmation_revalidation",
                error_code=exc.code.value,
                message=str(exc),
                mutation_may_have_started=False,
            )
            raise
        preview = self._preview_engine.build(plan, targets)
        review = self._validator.review(plan, preview)
        if not review.approved:
            error = ProcessActionError(
                ProcessActionErrorCode.PROCESS_IDENTITY_CHANGED,
                "; ".join(review.issues),
            )
            self._repository.transition(
                plan.transaction_id,
                ProcessActionState.BLOCKED,
                error_code=error.code,
                error_message=str(error),
            )
            self._audit.failed(
                plan,
                phase="runtime_confirmation_safety_review",
                error_code=error.code.value,
                message=str(error),
                mutation_may_have_started=False,
            )
            raise error
        request = self._confirmation.request_runtime(
            plan_confirmation_id,
            plan,
            preview,
        )
        self._repository.bind_runtime_preview(plan.transaction_id, preview)
        self._repository.record_confirmation(request)
        self._repository.bind_runtime_confirmation(plan.transaction_id, request.confirmation_id)
        return preview, request

    def resolve_runtime_confirmation(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: ProcessActionPlan,
        preview: ProcessActionPreview,
    ) -> ProcessActionConfirmation:
        """Resolve the immediate approval without permitting action substitution."""
        resolved = self._confirmation.resolve_runtime(confirmation_id, approved, plan, preview)
        self._repository.record_confirmation(resolved)
        self._audit.confirmation_resolved(plan, resolved)
        self._repository.transition(
            plan.transaction_id,
            ProcessActionState.CONFIRMED if approved else ProcessActionState.CANCELLED,
        )
        return resolved

    def execute(
        self,
        plan_confirmation_id: UUID,
        runtime_confirmation_id: UUID,
        plan: ProcessActionPlan,
        preview: ProcessActionPreview,
        cancellation: CancellationToken | None = None,
    ) -> ProcessActionToolResult:
        """Consume approval, revalidate, invoke the registered tool, verify, and audit."""
        token = cancellation or CancellationToken()
        consumed = self._confirmation.consume_runtime(runtime_confirmation_id, plan, preview)
        self._repository.consume_confirmation_pair(plan_confirmation_id, consumed)
        self._repository.record_confirmation(consumed)
        self._repository.transition(plan.transaction_id, ProcessActionState.VALIDATING)
        try:
            targets = self._revalidate_targets(plan)
        except ProcessActionError as exc:
            if exc.code is ProcessActionErrorCode.PROCESS_ALREADY_EXITED:
                already_result = _already_exited_result(plan)
                self._repository.transition(
                    plan.transaction_id,
                    ProcessActionState.ALREADY_EXITED,
                    result=already_result.model_dump(mode="json"),
                )
                self._audit.completed(plan, already_result)
                return already_result
            self._repository.transition(
                plan.transaction_id,
                ProcessActionState.BLOCKED,
                error_code=exc.code,
                error_message=str(exc),
            )
            self._audit.failed(
                plan,
                phase="execution_revalidation",
                error_code=exc.code.value,
                message=str(exc),
                mutation_may_have_started=False,
            )
            raise
        current_preview = self._preview_engine.build(plan, targets)
        review = self._validator.review(plan, current_preview)
        if (
            not review.approved
            or current_preview.target_set_digest != preview.target_set_digest
            or tuple(item.safety_class for item in current_preview.assessments)
            != tuple(item.safety_class for item in preview.assessments)
        ):
            message = "Process state changed after confirmation; generate a new Preview"
            self._repository.transition(
                plan.transaction_id,
                ProcessActionState.BLOCKED,
                error_code=ProcessActionErrorCode.PROCESS_IDENTITY_CHANGED,
                error_message=message,
            )
            self._audit.failed(
                plan,
                phase="execution_safety_review",
                error_code=ProcessActionErrorCode.PROCESS_IDENTITY_CHANGED.value,
                message=message,
                mutation_may_have_started=False,
            )
            raise ProcessActionError(
                ProcessActionErrorCode.PROCESS_IDENTITY_CHANGED,
                message,
            )
        tool_name = _tool_name(plan.action)
        arguments = self._arguments(plan)
        running_state = (
            ProcessActionState.REQUESTING_GRACEFUL_EXIT
            if plan.action is ProcessActionType.REQUEST_GRACEFUL_EXIT
            else ProcessActionState.FORCE_TERMINATING
        )
        self._repository.transition(plan.transaction_id, running_state)
        try:
            self._audit.started(
                plan,
                current_preview,
                str(runtime_confirmation_id),
                tool_name,
            )
        except Exception:
            self._repository.transition(
                plan.transaction_id,
                ProcessActionState.FAILED,
                error_code=ProcessActionErrorCode.AUDIT_UNAVAILABLE,
                error_message="Mandatory pre-execution audit could not be recorded",
            )
            raise
        authorization = ExecutionAuthorization(
            transaction_id=plan.transaction_id,
            operation_id=plan.operation_id,
            plan_id=plan.plan_id,
            preview_id=preview.preview_id,
            tool_name=tool_name,
            arguments_digest=arguments_digest(arguments),
            runtime_confirmation_id=runtime_confirmation_id,
        )
        try:
            result = self._registry.execute(
                tool_name,
                arguments,
                token,
                authorization=authorization,
            )
        except Exception as exc:
            self._repository.transition(
                plan.transaction_id,
                ProcessActionState.UNKNOWN,
                error_code=ProcessActionErrorCode.PLATFORM_ERROR,
                error_message=f"Unexpected tool failure: {type(exc).__name__}",
            )
            self._audit.failed(
                plan,
                phase="platform_execution",
                error_code=ProcessActionErrorCode.PLATFORM_ERROR.value,
                message=f"{type(exc).__name__}: {exc}",
                mutation_may_have_started=True,
            )
            raise
        if not isinstance(result, ProcessActionToolResult):
            raise TypeError("Process tool returned an invalid result")
        terminal, error = _terminal_state(plan.action, result)
        self._repository.transition(
            plan.transaction_id,
            terminal,
            error_code=error,
            error_message=None if error is None else "Process action did not fully complete",
            result=result.model_dump(mode="json"),
        )
        self._audit.completed(plan, result)
        return result

    def prepare_force_after_graceful(
        self,
        graceful_plan: ProcessActionPlan,
    ) -> tuple[ProcessActionPlan, ProcessActionPreview, ProcessSafetyReview]:
        """Create a new high-impact transaction; no graceful approval is reused."""
        refreshed: list[ResolvedProcessTarget] = []
        if graceful_plan.target_query.include_application_group:
            for target in graceful_plan.targets:
                current = self._resolver.resolve_current_application_group(target)
                if current is not None:
                    refreshed.append(current)
        else:
            for target in graceful_plan.targets:
                current_members = tuple(
                    observation
                    for member in target.members
                    if (observation := self._resolver.inspect_pid(member.identity.pid)) is not None
                    and observation.identity.canonical_digest()
                    == member.identity.canonical_digest()
                )
                if current_members:
                    refreshed.append(target.model_copy(update={"members": current_members}))
        if not refreshed:
            raise ProcessActionError(
                ProcessActionErrorCode.PROCESS_ALREADY_EXITED,
                "The original application has already exited; force termination is unnecessary",
            )
        plan = self._compiler.compile_resolved(
            f"Force terminate after graceful result: {graceful_plan.user_goal}",
            graceful_plan.target_query,
            ProcessActionType.FORCE_TERMINATE,
            tuple(refreshed),
            parent_transaction_id=graceful_plan.transaction_id,
        )
        return self.prepare_plan(plan)

    def _arguments(self, plan: ProcessActionPlan) -> dict[str, object]:
        timeout = (
            self._graceful_timeout
            if plan.action is ProcessActionType.REQUEST_GRACEFUL_EXIT
            else self._force_timeout
        )
        request = ProcessActionRequest(
            action=plan.action,
            identities=tuple(
                member.identity
                for target in plan.targets
                for member in sorted(
                    target.members,
                    key=lambda value: value.graceful_supported,
                    reverse=True,
                )
            ),
            target_set_digest=plan.target_set_digest(),
            timeout_seconds=timeout,
        )
        return request.model_dump(mode="json")

    def _revalidate_targets(
        self,
        plan: ProcessActionPlan,
    ) -> tuple[ResolvedProcessTarget, ...]:
        if plan.target_query.include_application_group:
            return tuple(self._resolver.re_resolve(target) for target in plan.targets)
        refreshed: list[ResolvedProcessTarget] = []
        for target in plan.targets:
            members = []
            for member in target.members:
                current = self._resolver.inspect_pid(member.identity.pid)
                if current is None:
                    raise ProcessActionError(
                        ProcessActionErrorCode.PROCESS_ALREADY_EXITED,
                        "The original process exited before execution; no action is needed",
                    )
                if current.identity.canonical_digest() != member.identity.canonical_digest():
                    raise ProcessActionError(
                        ProcessActionErrorCode.PROCESS_IDENTITY_CHANGED,
                        "PID now identifies a different process",
                    )
                members.append(current)
            refreshed.append(target.model_copy(update={"members": tuple(members)}))
        return tuple(refreshed)


def _tool_name(action: ProcessActionType) -> str:
    return (
        "system.process.request_exit"
        if action is ProcessActionType.REQUEST_GRACEFUL_EXIT
        else "system.process.force_terminate"
    )


def _terminal_state(
    action: ProcessActionType,
    result: ProcessActionToolResult,
) -> tuple[ProcessActionState, ProcessActionErrorCode | None]:
    states = {item.state for item in result.members}
    if states <= {ProcessMemberResultState.ALREADY_EXITED}:
        return ProcessActionState.ALREADY_EXITED, None
    if ProcessMemberResultState.IDENTITY_CHANGED in states:
        return ProcessActionState.BLOCKED, ProcessActionErrorCode.PROCESS_IDENTITY_CHANGED
    if ProcessMemberResultState.ACCESS_DENIED in states:
        return ProcessActionState.FAILED, ProcessActionErrorCode.PROCESS_ACCESS_DENIED
    if ProcessMemberResultState.NOT_ATTEMPTED in states:
        return ProcessActionState.FAILED, ProcessActionErrorCode.PLATFORM_ERROR
    if ProcessMemberResultState.CANCELLED_WAITING in states:
        if states <= {ProcessMemberResultState.CANCELLED_WAITING}:
            return ProcessActionState.CANCELLED, None
        if states <= {
            ProcessMemberResultState.EXITED,
            ProcessMemberResultState.ALREADY_EXITED,
            ProcessMemberResultState.CANCELLED_WAITING,
        }:
            return ProcessActionState.CANCELLED, None
        return ProcessActionState.FAILED, ProcessActionErrorCode.PLATFORM_ERROR
    if ProcessMemberResultState.STILL_RUNNING in states:
        if action is ProcessActionType.REQUEST_GRACEFUL_EXIT:
            return ProcessActionState.GRACEFUL_TIMEOUT, ProcessActionErrorCode.GRACEFUL_EXIT_TIMEOUT
        return ProcessActionState.UNKNOWN, ProcessActionErrorCode.PLATFORM_ERROR
    if ProcessMemberResultState.UNSUPPORTED in states:
        if action is ProcessActionType.REQUEST_GRACEFUL_EXIT:
            return (
                ProcessActionState.GRACEFUL_TIMEOUT,
                ProcessActionErrorCode.GRACEFUL_EXIT_TIMEOUT,
            )
        return ProcessActionState.BLOCKED, ProcessActionErrorCode.UNSUPPORTED_GRACEFUL_EXIT
    if result.all_exited:
        return (
            ProcessActionState.GRACEFUL_COMPLETED
            if action is ProcessActionType.REQUEST_GRACEFUL_EXIT
            else ProcessActionState.COMPLETED,
            None,
        )
    return ProcessActionState.FAILED, ProcessActionErrorCode.PLATFORM_ERROR


def _already_exited_result(plan: ProcessActionPlan) -> ProcessActionToolResult:
    current = datetime.now(UTC)
    return ProcessActionToolResult(
        action=plan.action,
        members=tuple(
            ProcessMemberResult(
                identity_digest=member.identity.canonical_digest(),
                pid=member.identity.pid,
                state=ProcessMemberResultState.ALREADY_EXITED,
                message="The original process already exited; no mutation was performed",
            )
            for target in plan.targets
            for member in target.members
        ),
        started_at=current,
        completed_at=current,
    )
