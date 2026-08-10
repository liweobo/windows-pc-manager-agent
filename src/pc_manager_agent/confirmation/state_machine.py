"""In-memory confirmation state machine with digest and expiry binding."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pc_manager_agent.confirmation.models import (
    ConfirmationKind,
    ConfirmationRequest,
    ConfirmationState,
)
from pc_manager_agent.domain.plans import PlanStep, TaskPlan


class ConfirmationError(RuntimeError):
    """Base confirmation validation failure."""


class ConfirmationService:
    """Issue and resolve confirmations without trusting UI state."""

    """负责把用户点击“批准”转换成一份与具体计划、具体步骤、具体参数和确认期限绑定的可验证授权.解决的核心问题：执行器不能只相信界面上的“已确认”状态，而必须能够证明用户批准的就是当前准备执行的这份计划。"""

    def __init__(
        self,
        ttl_seconds: int = 300,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))
        self._requests: dict[UUID, ConfirmationRequest] = {}
        self._approved_plan_digests: dict[UUID, str] = {}
        self._approved_runtime: set[tuple[UUID, str, str]] = set()

    def request_plan(self, plan: TaskPlan, object_summary: str) -> ConfirmationRequest:
        """Create a new expiring plan-confirmation request."""
        """
        创建计划确认，生成的确认请求会绑定：
            plan_id
            plan_digest--计划摘要
            object_summary--操作对象摘要
            expires_at--过期时间
            唯一的confirmation_id
        """
        request = ConfirmationRequest(
            kind=ConfirmationKind.PLAN,
            plan_id=plan.plan_id,
            plan_digest=plan.canonical_digest(),
            object_summary=object_summary,
            expires_at=self._now() + timedelta(seconds=self._ttl_seconds),
        )
        self._requests[request.confirmation_id] = request
        return request

    def request_runtime(
        self,
        plan: TaskPlan,
        step: PlanStep,
        object_summary: str,
    ) -> ConfirmationRequest:
        """Create immediate confirmation for an already plan-approved step."""
        """
        创建即时确认请求:
            plan_id：所属计划
            plan_digest：完整计划摘要
            step_id：准备执行的具体步骤
            arguments_digest：该步骤参数摘要
            object_summary：让用户看到的操作对象描述
            expires_at：确认过期时间
            kind=RUNTIME：标明是执行前即时确认
        """
        self.require_plan_approved(plan)
        request = ConfirmationRequest(
            kind=ConfirmationKind.RUNTIME,
            plan_id=plan.plan_id,
            plan_digest=plan.canonical_digest(),
            step_id=step.step_id,
            arguments_digest=step.arguments_digest(),
            object_summary=object_summary,
            expires_at=self._now() + timedelta(seconds=self._ttl_seconds),
        )
        self._requests[request.confirmation_id] = request
        return request

    def resolve(
        self,
        confirmation_id: UUID,
        approved: bool,
        plan: TaskPlan,
        step: PlanStep | None = None,
    ) -> ConfirmationRequest:
        """Validate identity, digest, arguments, and expiry before recording a decision."""
        try:
            request = self._requests[confirmation_id]
        except KeyError as exc:
            raise ConfirmationError("Unknown confirmation request") from exc
        if request.state is not ConfirmationState.PENDING:
            raise ConfirmationError("Confirmation was already resolved")
        if self._now() >= request.expires_at:
            expired = request.model_copy(update={"state": ConfirmationState.EXPIRED})
            self._requests[confirmation_id] = expired
            raise ConfirmationError("Confirmation expired")
        if request.plan_id != plan.plan_id or request.plan_digest != plan.canonical_digest():
            raise ConfirmationError("Confirmation does not match the current plan")
        if request.kind is ConfirmationKind.RUNTIME:
            if step is None or request.step_id != step.step_id:
                raise ConfirmationError("Runtime confirmation does not match the step")
            if request.arguments_digest != step.arguments_digest():
                raise ConfirmationError("Runtime confirmation arguments changed")
        state = ConfirmationState.APPROVED if approved else ConfirmationState.REJECTED
        resolved = request.model_copy(update={"state": state})
        self._requests[confirmation_id] = resolved
        if approved and request.kind is ConfirmationKind.PLAN:
            self._approved_plan_digests[plan.plan_id] = plan.canonical_digest()
        if approved and request.kind is ConfirmationKind.RUNTIME and step is not None:
            self._approved_runtime.add((plan.plan_id, step.step_id, step.arguments_digest()))
        return resolved

    def require_plan_approved(self, plan: TaskPlan) -> None:
        """Fail if no approval exists for the exact current plan snapshot."""
        """执行前检查总体授权"""
        if self._approved_plan_digests.get(plan.plan_id) != plan.canonical_digest():
            raise ConfirmationError("Plan is not confirmed or has changed")

    def require_runtime_approved(self, plan: TaskPlan, step: PlanStep) -> None:
        """Fail if immediate confirmation does not match exact step arguments."""
        """执行前检查步骤授权"""
        self.require_plan_approved(plan)
        binding = (plan.plan_id, step.step_id, step.arguments_digest())
        if binding not in self._approved_runtime:
            raise ConfirmationError("Runtime confirmation is missing or stale")
