"""Confirmation request and state models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class ConfirmationKind(StrEnum):
    """Two confirmation gates used by the application."""

    PLAN = "PLAN"
    RUNTIME = "RUNTIME"


class ConfirmationState(StrEnum):
    """Lifecycle state for an immutable confirmation request."""

    PENDING = "PENDING"  # 待处理
    APPROVED = "APPROVED"  # 已批准
    REJECTED = "REJECTED"  # 已拒绝
    EXPIRED = "EXPIRED"  # 已过期


class ConfirmationRequest(BaseModel):
    """Object-specific request bound to an immutable plan snapshot."""

    """
    confirmation_id	当前确认请求的唯一编号
    kind	        总体计划确认或运行时确认
    plan_id	        所属计划
    plan_digest	    完整计划的 SHA-256 摘要
    step_id	        运行时确认绑定的步骤
    arguments_digest	运行时确认绑定的步骤参数摘要
    object_summary	向用户展示的操作对象说明
    expires_at	    用户最晚必须在什么时候作出决定
    state	        当前确认状态
    """
    model_config = ConfigDict(extra="forbid", frozen=True)
    # extra="forbid" 拒绝未定义字段; frozen=True 阻止实例化后修改数据.
    confirmation_id: UUID = Field(default_factory=uuid4)
    kind: ConfirmationKind
    plan_id: UUID
    plan_digest: str = Field(min_length=64, max_length=64)
    step_id: str | None = None
    arguments_digest: str | None = None
    object_summary: str = Field(min_length=1, max_length=2_000)
    expires_at: datetime
    state: ConfirmationState = ConfirmationState.PENDING
