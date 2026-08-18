"""End-to-end service planning, confirmation, control, verification, and audit."""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from uuid import UUID

from pc_manager_agent.audit.service_actions import ServiceActionAuditLogger
from pc_manager_agent.confirmation.service_actions import (
    ServiceActionConfirmation,
    ServiceActionConfirmationService,
)
from pc_manager_agent.domain.service_actions import (
    ServiceActionPlan,
    ServiceActionPreview,
    ServiceActionResult,
    ServiceActionType,
    ServiceErrorCode,
    ServiceInventoryItem,
    ServiceState,
    ServiceStepRequest,
    ServiceStepResult,
    ServiceStepType,
    ServiceTransactionState,
)
from pc_manager_agent.domain.service_errors import ServiceActionError
from pc_manager_agent.orchestration.service_action_planner import ServiceActionPlanCompiler
from pc_manager_agent.orchestration.service_target_resolver import ServiceTargetResolver
from pc_manager_agent.persistence.service_actions import ServiceActionRepository
from pc_manager_agent.platform_support.service_control import ServiceControlPlatform
from pc_manager_agent.safety.service_policy import ServiceSafetyPolicy
from pc_manager_agent.safety.service_preview import ServicePreviewEngine
from pc_manager_agent.safety.service_validator import (
    ServiceActionSafetyValidator,
    ServiceSafetyReview,
)
from pc_manager_agent.tools.execution import ExecutionAuthorization, arguments_digest
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.registry import ToolRegistry


class ServiceActionService:
    """Coordinate service controls without trusting GUI or model-authored identity."""

    def __init__(
        self,
        platform: ServiceControlPlatform,
        resolver: ServiceTargetResolver,
        compiler: ServiceActionPlanCompiler,
        policy: ServiceSafetyPolicy,
        preview_engine: ServicePreviewEngine,
        validator: ServiceActionSafetyValidator,
        confirmation: ServiceActionConfirmationService,
        repository: ServiceActionRepository,
        registry: ToolRegistry,
        audit: ServiceActionAuditLogger,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Service action timeout must be positive")
        self._platform = platform
        self._resolver = resolver
        self._compiler = compiler
        self._policy = policy
        self._preview_engine = preview_engine
        self._validator = validator
        self._confirmation = confirmation
        self._repository = repository
        self._registry = registry
        self._audit = audit
        self._timeout = timeout_seconds

    def list_current(self) -> tuple[ServiceInventoryItem, ...]:
        """Return fresh inventory with three action-specific read-only assessments."""
        items: list[ServiceInventoryItem] = []
        for observation in self._resolver.list_current():
            permissions = {
                action: self._platform.evaluate_permissions(
                    observation.identity.service_name, action
                )
                for action in ServiceActionType
            }
            assessments = {
                action: self._policy.assess(observation, action) for action in ServiceActionType
            }
            allowed = {
                action: (
                    assessments[action].decision.value == "ALLOW"
                    and permissions[action].allows(action)
                    and (
                        observation.state is ServiceState.STOPPED
                        if action is ServiceActionType.START
                        else observation.state is ServiceState.RUNNING
                    )
                )
                for action in ServiceActionType
            }
            explanations = tuple(
                dict.fromkeys(
                    assessment.explanation
                    for assessment in assessments.values()
                    if assessment.decision.value != "ALLOW"
                )
            )
            items.append(
                ServiceInventoryItem(
                    observation=observation,
                    safety_class=assessments[ServiceActionType.STOP].safety_class,
                    start_allowed=allowed[ServiceActionType.START],
                    stop_allowed=allowed[ServiceActionType.STOP],
                    restart_allowed=allowed[ServiceActionType.RESTART],
                    explanation=(
                        "; ".join(explanations)
                        if explanations
                        else "Eligible actions still require Preview and two confirmations"
                    ),
                )
            )
        return tuple(items)

    def prepare(
        self,
        user_goal: str,
        target_query: str,
        action: ServiceActionType,
    ) -> tuple[ServiceActionPlan, ServiceActionPreview, ServiceSafetyReview]:
        """Resolve locally, inspect, classify, and persist one read-only Preview."""
        observation = self._resolver.resolve_query(target_query)
        permissions = self._platform.evaluate_permissions(observation.identity.service_name, action)
        plan = self._compiler.compile(user_goal, target_query, action, observation, permissions)
        preview = self._preview_engine.build(plan, observation, permissions)
        review = self._validator.review(plan, preview)
        steps = self._step_arguments(plan, observation.state)
        self._repository.create(plan, preview, steps)
        self._audit.previewed(plan, preview)
        self._repository.transition(
            plan.transaction_id,
            (
                ServiceTransactionState.AWAITING_CONFIRMATION
                if review.approved
                else ServiceTransactionState.BLOCKED
            ),
            error_code=(None if review.approved else _preview_error(preview)),
            error_message=(None if review.approved else "; ".join(review.issues)),
        )
        return plan, preview, review

    def request_plan_confirmation(
        self, plan: ServiceActionPlan, preview: ServiceActionPreview
    ) -> ServiceActionConfirmation:
        """Issue and persist the first exact service confirmation."""
        value = self._confirmation.request_plan(plan, preview)
        self._repository.record_confirmation(value)
        self._repository.bind_confirmation(
            plan.transaction_id, value.confirmation_id, runtime=False
        )
        return value

    def resolve_plan_confirmation(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: ServiceActionPlan,
        preview: ServiceActionPreview,
    ) -> ServiceActionConfirmation:
        """Resolve the plan gate; rejection cancels without SCM access."""
        value = self._confirmation.resolve_plan(confirmation_id, approved, plan, preview)
        self._repository.record_confirmation(value)
        self._audit.confirmation_resolved(plan, value)
        self._repository.transition(
            plan.transaction_id,
            (
                ServiceTransactionState.AWAITING_RUNTIME_CONFIRMATION
                if approved
                else ServiceTransactionState.CANCELLED
            ),
        )
        return value

    def request_runtime_confirmation(
        self,
        plan_confirmation_id: UUID,
        plan: ServiceActionPlan,
    ) -> tuple[ServiceActionPreview, ServiceActionConfirmation]:
        """Revalidate every binding before issuing the short-lived runtime gate."""
        preview, review = self._runtime_preview(plan)
        if not review.approved:
            self._block(plan, "; ".join(review.issues), _preview_error(preview))
        value = self._confirmation.request_runtime(plan_confirmation_id, plan, preview)
        self._repository.bind_runtime_preview(plan.transaction_id, preview)
        self._repository.record_confirmation(value)
        self._repository.bind_confirmation(plan.transaction_id, value.confirmation_id, runtime=True)
        return preview, value

    def resolve_runtime_confirmation(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: ServiceActionPlan,
        preview: ServiceActionPreview,
    ) -> ServiceActionConfirmation:
        """Resolve the immediate gate without permitting substitution."""
        value = self._confirmation.resolve_runtime(confirmation_id, approved, plan, preview)
        self._repository.record_confirmation(value)
        self._audit.confirmation_resolved(plan, value)
        self._repository.transition(
            plan.transaction_id,
            (ServiceTransactionState.CONFIRMED if approved else ServiceTransactionState.CANCELLED),
        )
        return value

    def execute(
        self,
        plan_confirmation_id: UUID,
        runtime_confirmation_id: UUID,
        plan: ServiceActionPlan,
        preview: ServiceActionPreview,
        cancellation: CancellationToken | None = None,
    ) -> ServiceActionResult:
        """Consume approval, run ordered steps, verify, persist, and audit."""
        token = cancellation or CancellationToken()
        consumed = self._confirmation.consume_runtime(runtime_confirmation_id, plan, preview)
        if consumed.parent_confirmation_id != plan_confirmation_id:
            raise ServiceActionError(
                ServiceErrorCode.CONFIRMATION_REPLAYED,
                "Runtime confirmation does not belong to the supplied plan confirmation",
            )
        self._repository.consume_confirmation_pair(plan_confirmation_id, consumed)
        self._repository.record_confirmation(consumed)
        self._repository.transition(plan.transaction_id, ServiceTransactionState.VALIDATING)
        runtime_preview, review = self._runtime_preview(plan)
        if (
            not review.approved
            or runtime_preview.current_state_digest != preview.current_state_digest
            or runtime_preview.dependencies.graph_digest != preview.dependencies.graph_digest
            or runtime_preview.permissions.canonical_digest()
            != preview.permissions.canonical_digest()
        ):
            self._block(
                plan,
                "Service identity, state, dependencies, or permissions changed after confirmation",
                ServiceErrorCode.SERVICE_STATE_CHANGED,
            )
        try:
            self._audit.started(plan, runtime_preview)
        except Exception:
            self._repository.transition(
                plan.transaction_id,
                ServiceTransactionState.FAILED,
                error_code=ServiceErrorCode.AUDIT_UNAVAILABLE,
                error_message="Mandatory service pre-execution audit failed",
            )
            raise
        return self._execute_steps(plan, runtime_preview, runtime_confirmation_id, token)

    def _execute_steps(
        self,
        plan: ServiceActionPlan,
        preview: ServiceActionPreview,
        runtime_confirmation_id: UUID,
        cancellation: CancellationToken,
    ) -> ServiceActionResult:
        started = datetime.now(UTC)
        results: list[ServiceStepResult] = []
        expected_state = preview.observation.state
        for index, step in enumerate(plan.steps):
            if cancellation.is_cancelled:
                return self._cancelled_result(plan, results, expected_state, started)
            tool_name, arguments = self._step_argument(plan, step, expected_state)
            executing = (
                ServiceTransactionState.EXECUTING_START
                if step is ServiceStepType.START
                else ServiceTransactionState.EXECUTING_STOP
            )
            self._repository.transition(plan.transaction_id, executing)
            self._repository.begin_step(plan.transaction_id, index, executing)
            authorization = ExecutionAuthorization(
                transaction_id=plan.transaction_id,
                operation_id=plan.operation_id,
                plan_id=plan.plan_id,
                preview_id=self._repository.get(plan.transaction_id).preview_id,
                tool_name=tool_name,
                arguments_digest=arguments_digest(arguments),
                runtime_confirmation_id=runtime_confirmation_id,
            )
            try:
                result = self._registry.execute(
                    tool_name,
                    arguments,
                    cancellation,
                    authorization=authorization,
                )
            except Exception as exc:
                partial = bool(results) and plan.action is ServiceActionType.RESTART
                terminal = (
                    ServiceTransactionState.PARTIALLY_COMPLETED
                    if partial
                    else ServiceTransactionState.FAILED
                )
                final_state = self._final_state(plan.target_identity.service_name, expected_state)
                partial_result = self._result(
                    plan,
                    tuple(results),
                    final_state,
                    started,
                    completed=False,
                    partially_completed=partial,
                    message=(
                        f"{step.value} failed after earlier restart steps; "
                        f"verified current state is {final_state.value}"
                    ),
                )
                self._repository.transition(
                    plan.transaction_id,
                    terminal,
                    error_code=getattr(exc, "code", ServiceErrorCode.PLATFORM_ERROR),
                    error_message=f"{type(exc).__name__}: {exc}",
                    result=partial_result.model_dump(mode="json"),
                )
                self._audit.failed(
                    plan,
                    phase=f"step_{index}_{step.value.lower()}",
                    error_code=getattr(
                        getattr(exc, "code", ServiceErrorCode.PLATFORM_ERROR),
                        "value",
                        ServiceErrorCode.PLATFORM_ERROR.value,
                    ),
                    message=f"{type(exc).__name__}: {exc}",
                    mutation_may_have_started=True,
                )
                if partial:
                    self._audit.completed(plan, partial_result)
                    return partial_result
                raise
            if not isinstance(result, ServiceStepResult):
                raise TypeError("Service tool returned an invalid result")
            results.append(result)
            self._audit.step_completed(plan, index, result)
            if not result.verified:
                terminal = (
                    ServiceTransactionState.PARTIALLY_COMPLETED
                    if results[:-1] and plan.action is ServiceActionType.RESTART
                    else ServiceTransactionState.FAILED
                )
                combined = self._result(
                    plan,
                    tuple(results),
                    result.after_state,
                    started,
                    completed=False,
                    partially_completed=terminal is ServiceTransactionState.PARTIALLY_COMPLETED,
                    message=result.message,
                )
                self._repository.complete_step(plan.transaction_id, index, terminal)
                self._repository.record_terminal_result(
                    plan.transaction_id, combined.model_dump(mode="json")
                )
                self._audit.completed(plan, combined)
                return combined
            expected_state = result.after_state
            completed_state = (
                ServiceTransactionState.STOP_COMPLETED
                if plan.action is ServiceActionType.RESTART and step is ServiceStepType.STOP
                else ServiceTransactionState.COMPLETED
            )
            self._repository.complete_step(plan.transaction_id, index, completed_state)
            if (
                plan.action is ServiceActionType.RESTART
                and step is ServiceStepType.STOP
                and cancellation.cancellation_requested()
            ):
                return self._cancelled_result(plan, results, ServiceState.STOPPED, started)
        final = self._result(
            plan,
            tuple(results),
            expected_state,
            started,
            completed=True,
            message="Service action reached its expected final state",
        )
        self._repository.record_terminal_result(plan.transaction_id, final.model_dump(mode="json"))
        self._audit.completed(plan, final)
        return final

    def _cancelled_result(
        self,
        plan: ServiceActionPlan,
        results: list[ServiceStepResult],
        final_state: ServiceState,
        started: datetime,
    ) -> ServiceActionResult:
        partial = bool(results) and plan.action is ServiceActionType.RESTART
        state = (
            ServiceTransactionState.PARTIALLY_COMPLETED
            if partial
            else ServiceTransactionState.CANCELLED
        )
        result = self._result(
            plan,
            tuple(results),
            final_state,
            started,
            completed=False,
            partially_completed=partial,
            message=(
                "Restart was cancelled after stop; service remains STOPPED"
                if partial
                else "Cancelled before the next SCM control request"
            ),
        )
        self._repository.transition(
            plan.transaction_id,
            state,
            error_code=(
                ServiceErrorCode.CANCELLED_AFTER_STOP
                if partial
                else ServiceErrorCode.CANCELLED_BEFORE_DISPATCH
            ),
            result=result.model_dump(mode="json"),
        )
        self._audit.completed(plan, result)
        return result

    def _runtime_preview(
        self, plan: ServiceActionPlan
    ) -> tuple[ServiceActionPreview, ServiceSafetyReview]:
        observation = self._resolver.resolve_name(plan.target_identity.service_name)
        if observation.identity.canonical_digest() != plan.target_identity.canonical_digest():
            self._block(
                plan,
                "Service configuration changed",
                ServiceErrorCode.SERVICE_CONFIGURATION_CHANGED,
            )
        if observation.state_digest() != plan.expected_state_digest:
            self._block(
                plan,
                "Service state changed",
                ServiceErrorCode.SERVICE_STATE_CHANGED,
            )
        if observation.dependency_digest() != plan.expected_dependency_digest:
            self._block(
                plan,
                "Service dependencies changed",
                ServiceErrorCode.DEPENDENCY_GRAPH_CHANGED,
            )
        permissions = self._platform.evaluate_permissions(
            observation.identity.service_name, plan.action
        )
        if permissions.canonical_digest() != plan.expected_permission_digest:
            self._block(
                plan,
                "Service permissions changed",
                ServiceErrorCode.PRIVILEGE_REQUIRED,
            )
        preview = self._preview_engine.build(plan, observation, permissions)
        return preview, self._validator.review(plan, preview)

    def _final_state(self, service_name: str, fallback: ServiceState) -> ServiceState:
        """Read current state after a failed step; never invent successful completion."""
        try:
            observation = self._platform.inspect(service_name)
        except Exception:
            return ServiceState.UNKNOWN
        return observation.state if observation is not None else fallback

    def _step_arguments(
        self, plan: ServiceActionPlan, initial_state: ServiceState
    ) -> tuple[tuple[str, dict[str, object]], ...]:
        state = initial_state
        rows: list[tuple[str, dict[str, object]]] = []
        for step in plan.steps:
            rows.append(self._step_argument(plan, step, state))
            state = ServiceState.RUNNING if step is ServiceStepType.START else ServiceState.STOPPED
        return tuple(rows)

    def _step_argument(
        self,
        plan: ServiceActionPlan,
        step: ServiceStepType,
        expected_state: ServiceState,
    ) -> tuple[str, dict[str, object]]:
        request = ServiceStepRequest(
            transaction_id=plan.transaction_id,
            step=step,
            identity=plan.target_identity,
            expected_identity_digest=plan.target_identity.canonical_digest(),
            expected_startup_configuration_digest=(
                plan.target_startup_configuration.canonical_digest()
            ),
            expected_state=expected_state,
            timeout_seconds=self._timeout,
        )
        return (
            ("system.service.start" if step is ServiceStepType.START else "system.service.stop"),
            request.model_dump(mode="json"),
        )

    @staticmethod
    def _result(
        plan: ServiceActionPlan,
        steps: tuple[ServiceStepResult, ...],
        final_state: ServiceState,
        started: datetime,
        *,
        completed: bool,
        partially_completed: bool = False,
        message: str,
    ) -> ServiceActionResult:
        return ServiceActionResult(
            action=plan.action,
            transaction_id=plan.transaction_id,
            steps=steps,
            final_state=final_state,
            completed=completed,
            partially_completed=partially_completed,
            no_op=bool(steps) and all(not step.control_dispatched for step in steps),
            message=message,
            started_at=started,
            completed_at=datetime.now(UTC),
        )

    def _block(
        self,
        plan: ServiceActionPlan,
        message: str,
        code: ServiceErrorCode,
    ) -> None:
        with suppress(Exception):
            self._repository.transition(
                plan.transaction_id,
                ServiceTransactionState.BLOCKED,
                error_code=code,
                error_message=message,
            )
        raise ServiceActionError(code, message)


def _preview_error(preview: ServiceActionPreview) -> ServiceErrorCode:
    if preview.permissions.process_elevated:
        return ServiceErrorCode.ELEVATED_PROCESS_BLOCKED
    if not preview.permissions.allows(preview.action):
        return ServiceErrorCode.PRIVILEGE_REQUIRED
    if preview.safety.reason_codes:
        return preview.safety.reason_codes[0]
    if preview.dependencies.blocking_dependencies:
        return ServiceErrorCode.DEPENDENCY_NOT_RUNNING
    if preview.dependencies.blocking_dependents:
        return ServiceErrorCode.DEPENDENT_SERVICE_RUNNING
    return ServiceErrorCode.ACTION_NOT_SUPPORTED
