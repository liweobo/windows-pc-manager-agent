"""Immutable structured task-plan models."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel


class FrozenModel(BaseModel):
    """Base model that rejects unknown fields and mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskScope(FrozenModel):
    """Paths explicitly included in and excluded from a task."""

    """描述一项任务的“路径边界”：哪些路径明确允许包含，哪些路径明确排除"""
    included_paths: tuple[Path, ...]
    excluded_paths: tuple[Path, ...] = ()

    @model_validator(mode="after")
    def require_included_path(self) -> Self:
        """included_paths必须有至少1个值"""
        """Reject plans without an explicit positive scope."""
        if not self.included_paths:
            msg = "At least one included path is required"
            raise ValueError(msg)
        return self


class PlanStep(FrozenModel):
    """One deterministic registered-tool invocation."""

    """具体、可执行的工具调用步骤"""
    """
    step_id	        当前步骤在计划中的唯一标识
    tool_name	    要调用的注册工具名称
    description	    给用户和审计日志看的步骤说明
    arguments	    传给工具的具体参数
    risk_level	    当前步骤的风险等级
    requires_confirmation	执行该步骤前是否需要即时确认
    rollback_level	操作能否回滚以及回滚类型
    preconditions	执行前应满足的条件
    expected_postconditions	执行后预期成立的状态
    """
    step_id: str = Field(
        pattern=r"^step-[a-zA-Z0-9][a-zA-Z0-9_-]*$"
    )  # 正则表达式校验; 不匹配时抛出异常.
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    description: str = Field(min_length=1, max_length=500)
    arguments: dict[str, JsonValue]
    risk_level: RiskLevel
    requires_confirmation: bool
    rollback_level: RollbackLevel
    preconditions: tuple[str, ...] = ()
    expected_postconditions: tuple[str, ...] = ()

    def arguments_digest(self) -> str:
        """Return a stable digest used to bind runtime confirmation."""
        """
        1.将参数转换为键排序稳定的JSON
        2.对JSON计算SHA-256
        3.返回固定摘要
        """
        payload = json.dumps(
            self.arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class EstimatedImpact(FrozenModel):
    """描述计划预计影响的文件数量, 供用户和安全系统执行前审查."""

    """Conservative impact estimate for a proposed plan."""
    """
    files_read	    预计读取的文件数量
    files_modified	预计修改的文件数量
    files_deleted	预计删除的文件数量
    """
    files_read: int | None = Field(default=None, ge=0)
    files_modified: int = Field(default=0, ge=0)
    files_deleted: int = Field(default=0, ge=0)


class TaskPlan(FrozenModel):
    """Validated plan that is reviewed and confirmed before execution."""

    """
    plan_id	计划的唯一 ID
    plan_version	计划版本
    created_at	创建时间
    summary	计划摘要
    user_goal	用户的原始目标
    assumptions	执行计划依赖的假设
    scope	允许和排除的路径范围
    steps	准备执行的工具步骤
    estimated_impact	预计读取、修改和删除的文件数量
    requires_plan_confirmation	是否需要用户确认总体计划
    requires_runtime_confirmation	执行过程中是否还需要即时确认
    """
    plan_id: UUID = Field(default_factory=uuid4)
    plan_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: str = Field(min_length=1, max_length=500)
    user_goal: str = Field(min_length=1, max_length=2_000)
    assumptions: tuple[str, ...] = ()
    scope: TaskScope
    steps: tuple[PlanStep, ...]
    estimated_impact: EstimatedImpact = Field(default_factory=EstimatedImpact)
    requires_plan_confirmation: bool = True
    requires_runtime_confirmation: bool = False

    @model_validator(mode="after")
    def validate_steps(self) -> Self:
        """Require a non-empty plan and unique step identifiers."""
        """
        强制计划结构有效
            1.至少包含一个执行步骤
            2.每个步骤的 step_id 必须唯一
        """
        if not self.steps:
            msg = "At least one plan step is required"
            raise ValueError(msg)
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            msg = "Plan step identifiers must be unique"
            raise ValueError(msg)
        return self

    def canonical_digest(self) -> str:
        """Hash every execution-relevant plan field for confirmation binding."""
        """把完整计划转换成排序稳定的 JSON，然后计算 SHA-256 摘要"""
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
