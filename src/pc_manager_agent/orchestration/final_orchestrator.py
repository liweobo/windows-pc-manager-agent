"""Stage 5E Final Orchestrator for safe, recoverable high-level coordination."""

from __future__ import annotations

import re
import threading
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pc_manager_agent.audit.computer_tasks import ComputerTaskAuditLogger
from pc_manager_agent.config.tasks import FinalTaskLimits
from pc_manager_agent.domain.computer_tasks import (
    AutonomyLevel,
    ComputerTask,
    ComputerTaskEvent,
    ComputerTaskKind,
    ComputerTaskState,
    TaskBudget,
    TaskPlanConfirmation,
    TaskPlanConfirmationState,
    TaskPolicySnapshot,
    TaskProgress,
    TaskRevisionRequest,
)
from pc_manager_agent.domain.risk import RiskLevel
from pc_manager_agent.domain.task_attention import UserAttentionItem, UserAttentionKind
from pc_manager_agent.domain.task_checkpoints import (
    TaskCheckpoint,
    TaskDispatchState,
    TaskNodeDispatch,
    TaskNodeSnapshot,
)
from pc_manager_agent.domain.task_graph import (
    TaskDependency,
    TaskGraph,
    TaskNode,
    TaskNodeStatus,
    TaskNodeType,
)
from pc_manager_agent.domain.task_summaries import StructuredTaskSummary
from pc_manager_agent.domain.task_workflows import (
    DomainPreparationResult,
    DomainPreparationStatus,
    DomainReconciliationRequest,
    DomainReconciliationResult,
    DomainResultReceipt,
    DomainResultStatus,
    DomainType,
    DomainWorkflowRequest,
)
from pc_manager_agent.orchestration.domain_workflows import DomainWorkflowRegistry
from pc_manager_agent.orchestration.task_attention import UserAttentionQueue
from pc_manager_agent.orchestration.task_graphs import (
    FinalTaskGraphBuilder,
    domain_type_for_node,
)
from pc_manager_agent.orchestration.task_state_machine import TaskStateMachine
from pc_manager_agent.orchestration.task_summary import TaskSummaryBuilder
from pc_manager_agent.persistence.computer_tasks import ComputerTaskRepository
from pc_manager_agent.safety.final_orchestrator import (
    GlobalSafetyInvariantGuard,
    SafeTaskSummaryPolicy,
    TaskPlanConfirmationPolicy,
    canonical_scope_digest,
)
from pc_manager_agent.safety.task_revision import TaskRevisionValidator


class FinalOrchestratorError(RuntimeError):
    """Raised when coordination cannot proceed without weakening a domain boundary."""


class FinalOrchestrator:
    """Coordinate durable high-level handoffs without executing domain tools."""

    def __init__(
        self,
        repository: ComputerTaskRepository,
        workflows: DomainWorkflowRegistry,
        graph_builder: FinalTaskGraphBuilder,
        limits: FinalTaskLimits,
        policy: TaskPolicySnapshot,
        audit: ComputerTaskAuditLogger,
    ) -> None:
        self._repository = repository
        self._workflows = workflows
        self._graph_builder = graph_builder
        self._limits = limits
        self._policy = policy
        self._audit = audit
        self._state_machine = TaskStateMachine()
        self._summary_policy = SafeTaskSummaryPolicy()
        self._confirmation_policy = TaskPlanConfirmationPolicy()
        self._guard = GlobalSafetyInvariantGuard()
        self._revision_validator = TaskRevisionValidator()
        self._attention = UserAttentionQueue(repository)
        self._summaries = TaskSummaryBuilder(workflows)
        self._volatile_goals: dict[UUID, str] = {}
        self._lock = threading.RLock()

    def create_task(
        self,
        goal: str,
        domains: tuple[DomainType, ...],
        *,
        root_request_id: UUID,
        kind: ComputerTaskKind,
        autonomy: AutonomyLevel,
        conversation_id: UUID | None = None,
    ) -> ComputerTask:
        """Create, validate, checkpoint, and expose one task plan for user review."""
        self._guard.require_guided_autonomy(autonomy)
        graph = self._graph_builder.build(goal, domains, kind)
        graph_id = uuid4()
        persisted = self._graph_builder.persistable(graph, graph_id)
        now = datetime.now(UTC)
        task = ComputerTask(
            task_id=graph.task_id,
            root_request_id=root_request_id,
            conversation_id=conversation_id,
            safe_goal_summary=self._summary_policy.summarize(goal),
            goal_digest=graph.goal_digest,
            task_kind=kind,
            autonomy_level=autonomy,
            graph_id=graph_id,
            graph_version=graph.version,
            graph_digest=graph.canonical_digest(),
            policy=self._policy,
            budget=self._task_budget(),
            progress=TaskProgress(
                planned_steps=len(graph.nodes),
                current_phase_code="CREATED",
            ),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._repository.create_task(task, persisted)
            self._repository.save_checkpoint(self._initial_checkpoint(task, graph.nodes))
            task = self._transition(task, ComputerTaskState.PLANNING, "TASK_PLANNING")
            task = self._transition(
                task,
                ComputerTaskState.AWAITING_PLAN_CONFIRMATION,
                "TASK_AWAITING_PLAN_CONFIRMATION",
            )
            self._volatile_goals[task.task_id] = goal
        return task

    def request_plan_confirmation(
        self,
        task_id: UUID,
        *,
        scope_references: tuple[str, ...] = (),
    ) -> TaskPlanConfirmation:
        """Create exact task-plan consent covering coordination R0 nodes only."""
        with self._lock:
            task = self._repository.get_task(task_id)
            if any(
                item.kind is UserAttentionKind.PLAN_CONFIRMATION
                for item in self._attention.pending(task_id)
            ):
                raise FinalOrchestratorError("Task already has an unresolved plan confirmation")
            graph = self._require_volatile_graph(task)
            confirmation = self._confirmation_policy.request(
                task,
                graph,
                scope_digest=canonical_scope_digest(scope_references),
                ttl_seconds=self._limits.plan_confirmation_ttl_seconds,
            )
            self._repository.save_plan_confirmation(confirmation)
            self._enqueue_attention(
                task,
                kind=UserAttentionKind.PLAN_CONFIRMATION,
                risk=RiskLevel.R0,
                title_code="TASK_PLAN_CONFIRMATION_REQUIRED",
                summary=(
                    "确认此任务的只读协调计划；具体文件、系统、浏览器或办公操作仍需各自确认。"
                ),
            )
        return confirmation

    def resolve_plan_confirmation(
        self,
        confirmation_id: UUID,
        *,
        approved: bool,
    ) -> ComputerTask:
        """Resolve task-plan consent once; rejection cancels future coordination."""
        with self._lock:
            confirmation = self._repository.get_plan_confirmation(confirmation_id)
            task = self._repository.get_task(confirmation.task_id)
            resolved = self._confirmation_policy.resolve(
                confirmation,
                task,
                approved=approved,
            )
            self._repository.update_plan_confirmation(
                resolved,
                expected=TaskPlanConfirmationState.PENDING,
            )
            self._resolve_attention_kind(task.task_id, UserAttentionKind.PLAN_CONFIRMATION)
            if resolved.state is TaskPlanConfirmationState.APPROVED:
                return self._transition(task, ComputerTaskState.READY, "TASK_PLAN_APPROVED")
            target = (
                ComputerTaskState.CANCELLED
                if resolved.state is TaskPlanConfirmationState.REJECTED
                else ComputerTaskState.BLOCKED
            )
            return self._transition(task, target, f"TASK_PLAN_{resolved.state.value}")

    def start(self, task_id: UUID, confirmation_id: UUID) -> ComputerTask:
        """Consume approved task-plan consent and start coordination, never domain execution."""
        with self._lock:
            task = self._repository.get_task(task_id)
            confirmation = self._repository.get_plan_confirmation(confirmation_id)
            if task.state is not ComputerTaskState.READY:
                raise FinalOrchestratorError("Task is not ready")
            if (
                confirmation.task_id != task_id
                or confirmation.state is not TaskPlanConfirmationState.APPROVED
                or confirmation.graph_version != task.graph_version
                or confirmation.graph_digest != task.graph_digest
            ):
                raise FinalOrchestratorError("Task-plan confirmation is stale or not approved")
            consumed = confirmation.model_copy(update={"state": TaskPlanConfirmationState.CONSUMED})
            self._repository.update_plan_confirmation(
                consumed,
                expected=TaskPlanConfirmationState.APPROVED,
            )
            return self._transition(task, ComputerTaskState.RUNNING, "TASK_STARTED")

    def advance(self, task_id: UUID) -> DomainPreparationResult | StructuredTaskSummary | None:
        """Advance one node; domain nodes stop at the existing high-level workflow boundary."""
        with self._lock:
            task = self._repository.get_task(task_id)
            if task.state is not ComputerTaskState.RUNNING:
                raise FinalOrchestratorError("Task must be RUNNING before it can advance")
            exceeded = task.usage.exceeded(task.budget)
            if exceeded:
                paused = self._transition(
                    task,
                    ComputerTaskState.PAUSED,
                    "TASK_BUDGET_PAUSED",
                    detail_codes=exceeded,
                )
                self._enqueue_attention(
                    paused,
                    kind=UserAttentionKind.BUDGET_EXCEEDED,
                    risk=RiskLevel.R0,
                    title_code="TASK_BUDGET_EXCEEDED",
                    summary="任务预算已达到上限，未继续派发；请检查范围后再决定。",
                )
                return None
            graph = self._repository.get_graph(task_id, task.graph_version)
            checkpoint = self._repository.latest_checkpoint(task_id)
            node = self._next_ready_node(graph.nodes, graph.dependencies, checkpoint.nodes)
            if node is None:
                return self._finish(task, checkpoint)
            if node.node_type is TaskNodeType.UNDERSTAND:
                self._complete_internal_node(task, checkpoint, node, "TASK_GOAL_UNDERSTOOD")
                return None
            if node.node_type is TaskNodeType.SUMMARIZE:
                self._complete_internal_node(task, checkpoint, node, "TASK_SUMMARY_BUILT")
                current = self._repository.get_task(task_id)
                latest = self._repository.latest_checkpoint(task_id)
                return self._finish(current, latest)
            domain = domain_type_for_node(node)
            if domain is None:
                raise FinalOrchestratorError("Only internal nodes may omit a domain")
            dispatch = self._repository.reserve_dispatch(
                TaskNodeDispatch(
                    task_id=task_id,
                    graph_version=task.graph_version,
                    node_id=node.node_id,
                    domain=domain,
                    read_only=True,
                )
            )
            self._audit.dispatch(
                task_id,
                node.node_id,
                event_code="DOMAIN_HANDOFF_RESERVED",
                dispatch_id=dispatch.dispatch_id,
                domain_code=domain.value,
                attempt_count=dispatch.attempt_count,
            )
            dispatched = dispatch.model_copy(
                update={
                    "state": TaskDispatchState.DISPATCHED,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._repository.save_dispatch(dispatched, TaskDispatchState.DISPATCHING)
            request = self._workflow_request(task, node, domain)
            try:
                prepared = self._workflows.require(domain).prepare(request)
                self._guard.validate_preparation(prepared)
            except Exception:
                failed_dispatch = dispatched.model_copy(
                    update={
                        "state": TaskDispatchState.RESOLVED,
                        "updated_at": datetime.now(UTC),
                    }
                )
                self._repository.save_dispatch(failed_dispatch, TaskDispatchState.DISPATCHED)
                self._set_node_status(task, checkpoint, node.node_id, TaskNodeStatus.FAILED)
                raise
            updated_dispatch = dispatched.model_copy(
                update={
                    "result_ref": str(prepared.preparation_id),
                    "updated_at": datetime.now(UTC),
                }
            )
            self._repository.save_dispatch(updated_dispatch, TaskDispatchState.DISPATCHED)
            return self._wait_for_domain(
                task,
                checkpoint,
                node,
                prepared,
                dispatch_id=updated_dispatch.dispatch_id,
            )

    def record_domain_receipt(self, receipt: DomainResultReceipt) -> ComputerTask:
        """Accept exact owning-domain truth and continue only after deterministic validation."""
        with self._lock:
            task = self._repository.get_task(receipt.task_id)
            if task.current_node_id is None:
                raise FinalOrchestratorError("Task has no active domain node")
            self._guard.validate_receipt(
                receipt,
                task_id=task.task_id,
                node_id=task.current_node_id,
                graph_version=task.graph_version,
            )
            graph = self._repository.get_graph(task.task_id, task.graph_version)
            node = next((item for item in graph.nodes if item.node_id == receipt.node_id), None)
            if node is None or domain_type_for_node(node) is not receipt.domain:
                raise FinalOrchestratorError("Domain receipt does not match the graph node")
            dispatch = next(
                (
                    item
                    for item in self._repository.unresolved_dispatches(task.task_id)
                    if item.node_id == receipt.node_id
                ),
                None,
            )
            if dispatch is None or dispatch.state is not TaskDispatchState.DISPATCHED:
                raise FinalOrchestratorError("Domain result has no active dispatch")
            self._repository.save_receipt(receipt)
            received = dispatch.model_copy(
                update={
                    "state": TaskDispatchState.RESULT_RECEIVED,
                    "domain_transaction_ref": (
                        receipt.domain_transaction_ref or dispatch.domain_transaction_ref
                    ),
                    "result_ref": receipt.result_ref,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._repository.save_dispatch(received, TaskDispatchState.DISPATCHED)
            resolved = received.model_copy(
                update={"state": TaskDispatchState.RESOLVED, "updated_at": datetime.now(UTC)}
            )
            self._repository.save_dispatch(resolved, TaskDispatchState.RESULT_RECEIVED)
            checkpoint = self._repository.latest_checkpoint(task.task_id)
            status = self._node_status_for_receipt(receipt)
            task = self._set_node_status(
                task,
                checkpoint,
                node.node_id,
                status,
                attempt_count=dispatch.attempt_count,
                result_ref=receipt.result_ref,
                domain_transaction_ref=receipt.domain_transaction_ref,
                dispatch_id=dispatch.dispatch_id,
            )
            self._resolve_attention_for_node(task.task_id, node.node_id)
            if task.state in {
                ComputerTaskState.WAITING_FOR_USER,
                ComputerTaskState.WAITING_FOR_DOMAIN_CONFIRMATION,
                ComputerTaskState.WAITING_FOR_USER_TAKEOVER,
            }:
                task = self._transition(task, ComputerTaskState.RUNNING, "DOMAIN_RESULT_ACCEPTED")
            return task

    def pause(self, task_id: UUID) -> ComputerTask:
        """Pause future scheduling; already dispatched external work is not killed."""
        with self._lock:
            task = self._repository.get_task(task_id)
            changed = self._transition(task, ComputerTaskState.PAUSED, "TASK_PAUSED")
            changed = changed.model_copy(update={"pause_requested": True})
            return self._save_same_revision_payload(changed)

    def resume(self, task_id: UUID) -> ComputerTask:
        """Require Fresh user review after pause instead of resuming old authority."""
        with self._lock:
            task = self._repository.get_task(task_id)
            running = self._transition(task, ComputerTaskState.RUNNING, "TASK_RESUME_REQUESTED")
            running = running.model_copy(update={"pause_requested": False})
            running = self._save_same_revision_payload(running)
            waiting = self._transition(
                running,
                ComputerTaskState.WAITING_FOR_USER,
                "TASK_RESUME_REQUIRES_FRESH_REVIEW",
            )
            self._enqueue_attention(
                waiting,
                kind=UserAttentionKind.MANUAL_REVIEW,
                risk=RiskLevel.R0,
                title_code="TASK_FRESH_REVIEW_REQUIRED",
                summary="暂停期间对象可能变化；请重新进入相关业务页面进行 Fresh 检查。",
            )
            return waiting

    def cancel(self, task_id: UUID) -> ComputerTask:
        """Cancel future nodes only; completed work and external apps are not undone or killed."""
        with self._lock:
            task = self._repository.get_task(task_id)
            if self._state_machine.is_terminal(task.state):
                raise FinalOrchestratorError("Terminal task cannot be cancelled again")
            task = self._save_same_revision_payload(
                task.model_copy(update={"cancellation_requested": True})
            )
            if task.state in {
                ComputerTaskState.RUNNING,
                ComputerTaskState.WAITING_FOR_USER,
                ComputerTaskState.WAITING_FOR_DOMAIN_CONFIRMATION,
                ComputerTaskState.WAITING_FOR_USER_TAKEOVER,
                ComputerTaskState.PAUSED,
            }:
                task = self._transition(task, ComputerTaskState.CANCELLING, "TASK_CANCELLING")
            return self._transition(
                task,
                ComputerTaskState.CANCELLED,
                "TASK_CANCELLED_FUTURE_ONLY",
            )

    def register_domain_transaction(
        self,
        task_id: UUID,
        node_id: UUID,
        transaction_ref: str,
    ) -> ComputerTask:
        """Checkpoint an opaque owning-domain transaction reference without authorizing it."""
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", transaction_ref) is None:
            raise FinalOrchestratorError("Domain transaction reference is invalid")
        with self._lock:
            task = self._repository.get_task(task_id)
            if task.current_node_id != node_id or task.state not in {
                ComputerTaskState.WAITING_FOR_USER,
                ComputerTaskState.WAITING_FOR_DOMAIN_CONFIRMATION,
                ComputerTaskState.WAITING_FOR_USER_TAKEOVER,
            }:
                raise FinalOrchestratorError("Task is not waiting on this domain node")
            dispatch = next(
                (
                    item
                    for item in self._repository.unresolved_dispatches(task_id)
                    if item.node_id == node_id and item.state is TaskDispatchState.DISPATCHED
                ),
                None,
            )
            if dispatch is None:
                raise FinalOrchestratorError("Domain node has no active dispatch")
            changed_dispatch = dispatch.model_copy(
                update={
                    "domain_transaction_ref": transaction_ref,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._repository.save_dispatch(changed_dispatch, TaskDispatchState.DISPATCHED)
            checkpoint = self._repository.latest_checkpoint(task_id)
            return self._set_node_status(
                task,
                checkpoint,
                node_id,
                TaskNodeStatus.RUNNING,
                event_code="DOMAIN_TRANSACTION_CORRELATED",
                attempt_count=dispatch.attempt_count,
                domain_transaction_ref=transaction_ref,
                dispatch_id=dispatch.dispatch_id,
            )

    def recover(self, task_id: UUID) -> tuple[DomainReconciliationResult, ...]:
        """Reconcile interrupted dispatches and wait for a user decision; never replay."""
        with self._lock:
            task = self._repository.get_task(task_id)
            recovering = self._transition(
                task,
                ComputerTaskState.RECOVERING,
                "TASK_RECOVERY_STARTED",
            )
            results: list[DomainReconciliationResult] = []
            for dispatch in self._repository.unresolved_dispatches(task_id):
                if dispatch.domain is None or dispatch.domain_transaction_ref is None:
                    continue
                reconciling = dispatch.model_copy(
                    update={
                        "state": TaskDispatchState.RECONCILING,
                        "updated_at": datetime.now(UTC),
                    }
                )
                if dispatch.state is not TaskDispatchState.RECONCILING:
                    self._repository.save_dispatch(reconciling, dispatch.state)
                request = DomainReconciliationRequest(
                    task_id=task_id,
                    node_id=dispatch.node_id,
                    graph_version=dispatch.graph_version,
                    domain=dispatch.domain,
                    domain_transaction_ref=dispatch.domain_transaction_ref,
                    previous_result_ref=dispatch.result_ref,
                )
                result = self._workflows.require(dispatch.domain).reconcile(request)
                if result.action_replayed:
                    raise FinalOrchestratorError("Recovery adapter attempted an action replay")
                results.append(result)
            waiting = self._transition(
                recovering,
                ComputerTaskState.WAITING_FOR_USER,
                "TASK_RECOVERY_REQUIRES_USER_DECISION",
            )
            self._enqueue_attention(
                waiting,
                kind=UserAttentionKind.RECOVERY_DECISION,
                risk=RiskLevel.R0,
                title_code="TASK_RECOVERY_REVIEW_REQUIRED",
                summary="应用已核对可用记录；旧确认全部失效，未自动重放任何操作。",
            )
            return tuple(results)

    def revise(self, request: TaskRevisionRequest) -> ComputerTask:
        """Create a new immutable graph version from one explicit user revision."""
        with self._lock:
            task = self._repository.get_task(request.task_id)
            old_graph = self._repository.get_graph(task.task_id, task.graph_version)
            old_domains = tuple(
                dict.fromkeys(
                    domain
                    for node in old_graph.nodes
                    if (domain := domain_type_for_node(node)) is not None
                )
            )
            domains = self._revision_validator.validate(
                task,
                request,
                current_domains=old_domains,
            )
            version = task.graph_version + 1
            graph = self._graph_builder.build(
                request.new_goal,
                domains,
                task.task_kind,
                version=version,
            ).model_copy(update={"task_id": task.task_id})
            graph_id = uuid4()
            persisted = self._graph_builder.persistable(graph, graph_id)
            invalidated = self._repository.invalidate_plan_confirmations(task.task_id)
            self._repository.save_graph(persisted)
            planning = self._transition(task, ComputerTaskState.PLANNING, "TASK_REVISION_STARTED")
            revised = planning.model_copy(
                update={
                    "safe_goal_summary": self._summary_policy.summarize(request.new_goal),
                    "goal_digest": graph.goal_digest,
                    "graph_id": graph_id,
                    "graph_version": version,
                    "graph_digest": graph.canonical_digest(),
                    "progress": TaskProgress(
                        planned_steps=len(graph.nodes),
                        current_phase_code="REVISED_PLAN",
                    ),
                    "current_node_id": None,
                }
            )
            revised = self._save_same_revision_payload(revised)
            self._repository.save_checkpoint(
                self._initial_checkpoint(
                    revised,
                    graph.nodes,
                    reason="TASK_GRAPH_REVISED",
                    invalidated_confirmation_count=invalidated,
                )
            )
            self._volatile_goals[task.task_id] = request.new_goal
            return self._transition(
                revised,
                ComputerTaskState.AWAITING_PLAN_CONFIRMATION,
                "TASK_REVISION_AWAITING_CONFIRMATION",
            )

    def list_recent(self, limit: int = 100) -> tuple[ComputerTask, ...]:
        """Return bounded durable summaries for Home, Task Center, and tray views."""
        return self._repository.list_recent(limit)

    def summary(self, task_id: UUID) -> StructuredTaskSummary:
        """Build a deterministic summary from durable owning-domain receipts."""
        task = self._repository.get_task(task_id)
        return self._summaries.build(task, self._repository.list_receipts(task_id))

    def pending_attention(self) -> tuple[UserAttentionItem, ...]:
        """Return notification-safe pending prompts; callers cannot resolve confirmations here."""
        return self._attention.pending()

    def close(self) -> None:
        """Drop volatile goals and close the independent Stage 5E repository."""
        with self._lock:
            self._volatile_goals.clear()
        self._repository.close()

    def _require_volatile_graph(self, task: ComputerTask) -> TaskGraph:
        goal = self._volatile_goals.get(task.task_id)
        if goal is None:
            raise FinalOrchestratorError("Raw goal is unavailable after restart; revise the task")
        persisted = self._repository.get_graph(task.task_id, task.graph_version)
        graph = self._graph_builder.build(
            goal,
            tuple(
                dict.fromkeys(
                    domain
                    for node in persisted.nodes
                    if (domain := domain_type_for_node(node)) is not None
                )
            ),
            task.task_kind,
            version=task.graph_version,
        ).model_copy(
            update={
                "task_id": task.task_id,
                "nodes": persisted.nodes,
                "dependencies": persisted.dependencies,
                "created_at": persisted.created_at,
            }
        )
        if graph.canonical_digest() != task.graph_digest:
            raise FinalOrchestratorError("Durable task graph integrity mismatch")
        return graph

    def _task_budget(self) -> TaskBudget:
        return TaskBudget(
            max_task_nodes=self._limits.max_task_nodes,
            max_agent_calls=self._limits.max_agent_calls,
            max_tool_preparations=self._limits.max_tool_preparations,
            max_browser_navigations=self._limits.max_browser_navigations,
            max_files_scanned=self._limits.max_files_scanned,
            max_runtime_seconds=self._limits.max_runtime_seconds,
            max_llm_calls=self._limits.max_llm_calls,
            max_delegation_depth=self._limits.max_delegation_depth,
        )

    def _initial_checkpoint(
        self,
        task: ComputerTask,
        nodes: tuple[TaskNode, ...],
        *,
        reason: str = "TASK_CREATED",
        invalidated_confirmation_count: int = 0,
    ) -> TaskCheckpoint:
        return TaskCheckpoint(
            task_id=task.task_id,
            graph_id=task.graph_id,
            graph_version=task.graph_version,
            graph_digest=task.graph_digest,
            policy_digest=task.policy.canonical_digest(),
            nodes=tuple(
                TaskNodeSnapshot(
                    node_id=node.node_id,
                    domain=domain_type_for_node(node),
                    status=TaskNodeStatus.PENDING,
                )
                for node in nodes
            ),
            checkpoint_reason=reason,
            invalidated_confirmation_count=invalidated_confirmation_count,
        )

    def _transition(
        self,
        task: ComputerTask,
        target: ComputerTaskState,
        event_code: str,
        *,
        detail_codes: tuple[str, ...] = (),
    ) -> ComputerTask:
        changed = self._state_machine.transition(task, target)
        self._repository.save_task(changed, expected_revision=task.revision)
        self._event(changed, event_code, detail_codes=detail_codes)
        return changed

    def _save_same_revision_payload(self, task: ComputerTask) -> ComputerTask:
        current = self._repository.get_task(task.task_id)
        if current.revision != task.revision:
            raise FinalOrchestratorError("Task payload changed concurrently")
        changed = task.model_copy(
            update={"revision": task.revision + 1, "updated_at": datetime.now(UTC)}
        )
        self._repository.save_task(changed, expected_revision=task.revision)
        return changed

    def _event(
        self,
        task: ComputerTask,
        event_code: str,
        *,
        node_id: UUID | None = None,
        detail_codes: tuple[str, ...] = (),
    ) -> None:
        event = ComputerTaskEvent(
            task_id=task.task_id,
            trace_id=task.trace_id,
            graph_version=task.graph_version,
            node_id=node_id,
            event_code=event_code,
            detail_codes=detail_codes,
        )
        self._repository.append_event(event)
        self._audit.lifecycle(
            task.task_id,
            event_code=event_code,
            state=task.state,
            graph_version=task.graph_version,
            graph_digest=task.graph_digest,
            node_id=node_id,
            detail_codes=detail_codes,
        )

    def _enqueue_attention(
        self,
        task: ComputerTask,
        *,
        kind: UserAttentionKind,
        risk: RiskLevel,
        title_code: str,
        summary: str,
        node_id: UUID | None = None,
        domain: DomainType | None = None,
    ) -> None:
        self._attention.enqueue(
            UserAttentionItem(
                task_id=task.task_id,
                node_id=node_id,
                graph_version=task.graph_version,
                kind=kind,
                domain=domain,
                risk_level=risk,
                title_code=title_code,
                summary=summary,
            )
        )

    def _resolve_attention_kind(self, task_id: UUID, kind: UserAttentionKind) -> None:
        for item in self._attention.pending(task_id):
            if item.kind is kind:
                self._attention.resolve(item.attention_id)

    def _resolve_attention_for_node(self, task_id: UUID, node_id: UUID) -> None:
        for item in self._attention.pending(task_id):
            if item.node_id == node_id:
                self._attention.resolve(item.attention_id)

    @staticmethod
    def _next_ready_node(
        nodes: tuple[TaskNode, ...],
        dependencies: tuple[TaskDependency, ...],
        snapshots: tuple[TaskNodeSnapshot, ...],
    ) -> TaskNode | None:
        statuses = {item.node_id: item.status for item in snapshots}
        terminal = {
            TaskNodeStatus.COMPLETED,
            TaskNodeStatus.PARTIAL,
            TaskNodeStatus.BLOCKED,
            TaskNodeStatus.CANCELLED,
        }
        for node in nodes:
            if statuses[node.node_id] is not TaskNodeStatus.PENDING:
                continue
            parents = tuple(
                edge.prerequisite_id for edge in dependencies if edge.dependent_id == node.node_id
            )
            if all(statuses[parent] in terminal for parent in parents):
                return node
        return None

    def _complete_internal_node(
        self,
        task: ComputerTask,
        checkpoint: TaskCheckpoint,
        node: TaskNode,
        event_code: str,
    ) -> ComputerTask:
        return self._set_node_status(
            task,
            checkpoint,
            node.node_id,
            TaskNodeStatus.COMPLETED,
            event_code=event_code,
        )

    def _set_node_status(
        self,
        task: ComputerTask,
        checkpoint: TaskCheckpoint,
        node_id: UUID,
        status: TaskNodeStatus,
        *,
        event_code: str = "TASK_NODE_UPDATED",
        attempt_count: int | None = None,
        result_ref: str | None = None,
        domain_transaction_ref: str | None = None,
        dispatch_id: UUID | None = None,
    ) -> ComputerTask:
        snapshots = tuple(
            item.model_copy(
                update={
                    "status": status,
                    "attempt_count": (
                        item.attempt_count if attempt_count is None else attempt_count
                    ),
                    "result_ref": item.result_ref if result_ref is None else result_ref,
                    "domain_transaction_ref": (
                        item.domain_transaction_ref
                        if domain_transaction_ref is None
                        else domain_transaction_ref
                    ),
                }
            )
            if item.node_id == node_id
            else item
            for item in checkpoint.nodes
        )
        completed = tuple(
            item.node_id
            for item in snapshots
            if item.status in {TaskNodeStatus.COMPLETED, TaskNodeStatus.PARTIAL}
        )
        progress = task.progress.model_copy(
            update={
                "completed_steps": sum(
                    item.status is TaskNodeStatus.COMPLETED for item in snapshots
                ),
                "failed_steps": sum(item.status is TaskNodeStatus.FAILED for item in snapshots),
                "blocked_steps": sum(item.status is TaskNodeStatus.BLOCKED for item in snapshots),
                "current_phase_code": event_code,
            }
        )
        changed = task.model_copy(
            update={
                "progress": progress,
                "current_node_id": node_id,
            }
        )
        changed = self._save_same_revision_payload(changed)
        self._repository.save_checkpoint(
            TaskCheckpoint(
                task_id=task.task_id,
                graph_id=task.graph_id,
                graph_version=task.graph_version,
                graph_digest=task.graph_digest,
                policy_digest=task.policy.canonical_digest(),
                nodes=snapshots,
                completed_node_ids=completed,
                current_node_id=node_id,
                node_result_refs=(
                    checkpoint.node_result_refs
                    if result_ref is None or result_ref in checkpoint.node_result_refs
                    else (*checkpoint.node_result_refs, result_ref)
                ),
                dispatch_ids=(
                    checkpoint.dispatch_ids
                    if dispatch_id is None or dispatch_id in checkpoint.dispatch_ids
                    else (*checkpoint.dispatch_ids, dispatch_id)
                ),
                pending_attention_ids=tuple(
                    item.attention_id for item in self._attention.pending(task.task_id)
                ),
                invalidated_confirmation_count=checkpoint.invalidated_confirmation_count,
                checkpoint_reason=event_code,
            )
        )
        self._event(changed, event_code, node_id=node_id)
        return changed

    def _workflow_request(
        self,
        task: ComputerTask,
        node: TaskNode,
        domain: DomainType,
    ) -> DomainWorkflowRequest:
        return DomainWorkflowRequest(
            task_id=task.task_id,
            node_id=node.node_id,
            graph_version=task.graph_version,
            domain=domain,
            goal_digest=task.goal_digest,
        )

    def _wait_for_domain(
        self,
        task: ComputerTask,
        checkpoint: TaskCheckpoint,
        node: TaskNode,
        prepared: DomainPreparationResult,
        *,
        dispatch_id: UUID,
    ) -> DomainPreparationResult:
        if prepared.status in {
            DomainPreparationStatus.BLOCKED,
            DomainPreparationStatus.FAILED,
            DomainPreparationStatus.CANCELLED,
        }:
            mapping = {
                DomainPreparationStatus.BLOCKED: TaskNodeStatus.BLOCKED,
                DomainPreparationStatus.FAILED: TaskNodeStatus.FAILED,
                DomainPreparationStatus.CANCELLED: TaskNodeStatus.CANCELLED,
            }
            changed = self._set_node_status(
                task, checkpoint, node.node_id, mapping[prepared.status]
            )
            self._transition(changed, ComputerTaskState.BLOCKED, "DOMAIN_PREPARATION_STOPPED")
            return prepared
        changed = self._set_node_status(
            task,
            checkpoint,
            node.node_id,
            TaskNodeStatus.RUNNING,
            event_code="DOMAIN_HANDOFF_READY",
            attempt_count=1,
            result_ref=str(prepared.preparation_id),
            dispatch_id=dispatch_id,
        )
        if prepared.status is DomainPreparationStatus.NEEDS_DOMAIN_CONFIRMATION:
            target = ComputerTaskState.WAITING_FOR_DOMAIN_CONFIRMATION
            kind = UserAttentionKind.DOMAIN_CONFIRMATION
            summary = "请在原业务页面查看对象级 Preview，并完成该业务域自己的确认。"
        elif prepared.status is DomainPreparationStatus.NEEDS_USER_TAKEOVER:
            target = ComputerTaskState.WAITING_FOR_USER_TAKEOVER
            kind = UserAttentionKind.USER_TAKEOVER
            summary = "该步骤需要可见的用户接管；接管不会沿用旧的页面引用或确认。"
        elif prepared.status is DomainPreparationStatus.NEEDS_TARGET_SELECTION:
            target = ComputerTaskState.WAITING_FOR_USER
            kind = UserAttentionKind.TARGET_SELECTION
            summary = "请在原业务页面明确选择对象；任务协调器不会替你选择。"
        else:
            target = ComputerTaskState.WAITING_FOR_USER
            kind = UserAttentionKind.MANUAL_REVIEW
            summary = "请进入原业务页面继续；该页面将独立执行 Fresh 检查、Preview 和确认。"
        waiting = self._transition(changed, target, "TASK_WAITING_FOR_DOMAIN")
        self._enqueue_attention(
            waiting,
            kind=kind,
            risk=prepared.risk_level,
            title_code=prepared.next_action_code or "OPEN_DOMAIN_WORKFLOW",
            summary=summary,
            node_id=node.node_id,
            domain=prepared.domain,
        )
        return prepared

    def _finish(
        self,
        task: ComputerTask,
        checkpoint: TaskCheckpoint,
    ) -> StructuredTaskSummary:
        statuses = {item.status for item in checkpoint.nodes}
        if statuses.intersection(
            {TaskNodeStatus.FAILED, TaskNodeStatus.BLOCKED, TaskNodeStatus.CANCELLED}
        ):
            target = ComputerTaskState.PARTIALLY_COMPLETED
        elif all(status is TaskNodeStatus.COMPLETED for status in statuses):
            target = ComputerTaskState.COMPLETED
        else:
            raise FinalOrchestratorError("Task has unfinished nodes")
        changed = self._transition(task, target, "TASK_FINISHED")
        self._volatile_goals.pop(task.task_id, None)
        return self._summaries.build(
            changed,
            self._repository.list_receipts(task.task_id),
        )

    @staticmethod
    def _node_status_for_receipt(receipt: DomainResultReceipt) -> TaskNodeStatus:
        return {
            DomainResultStatus.COMPLETED_VERIFIED: TaskNodeStatus.COMPLETED,
            DomainResultStatus.COMPLETED_UNVERIFIED: TaskNodeStatus.PARTIAL,
            DomainResultStatus.PARTIAL: TaskNodeStatus.PARTIAL,
            DomainResultStatus.BLOCKED: TaskNodeStatus.BLOCKED,
            DomainResultStatus.FAILED: TaskNodeStatus.FAILED,
            DomainResultStatus.CANCELLED: TaskNodeStatus.CANCELLED,
            DomainResultStatus.NOT_APPLICABLE: TaskNodeStatus.COMPLETED,
        }[receipt.status]
