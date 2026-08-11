"""SQLAlchemy-backed append-only audit repository."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import JsonValue
from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from pc_manager_agent.audit.models import AuditEvent
from pc_manager_agent.audit.redaction import redact_json, redact_text
from pc_manager_agent.persistence.database import create_sqlite_engine


class AuditUnavailableError(RuntimeError):
    """Raised when mandatory audit storage cannot be trusted."""


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""


class AuditEventRow(Base):
    """Internal relational representation of an audit event."""

    __tablename__ = "audit_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    original_request: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    plan_id: Mapped[str | None] = mapped_column(String(36), index=True)
    plan_version: Mapped[int | None] = mapped_column(Integer)
    agent_decision: Mapped[str | None] = mapped_column(Text)
    step_id: Mapped[str | None] = mapped_column(String(100))
    tool_name: Mapped[str | None] = mapped_column(String(120), index=True)
    parameters: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    risk_level: Mapped[str | None] = mapped_column(String(2), index=True)
    confirmation_required: Mapped[bool] = mapped_column(Boolean)
    confirmation_result: Mapped[str | None] = mapped_column(String(40))
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    rollback: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    verification: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    model_provider: Mapped[str | None] = mapped_column(String(80))
    model_request_id: Mapped[str | None] = mapped_column(String(200))
    app_version: Mapped[str] = mapped_column(String(40))
    git_commit: Mapped[str | None] = mapped_column(String(64))
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class AuditRepository:
    """Append redacted plan, confirmation, execution, verification, and error events."""

    def __init__(self, database_path: Path) -> None:
        self._engine = create_sqlite_engine(database_path)
        self._sessions = sessionmaker(
            self._engine, expire_on_commit=False
        )  # 创建会话工厂; expire_on_commit=False 表示事务结束后对象不过期
        self._initialized = False

    def initialize(self) -> None:
        """Create schema and verify that SQLite accepts a query."""
        """
        1.创建“audit_events”数据表
        2.执行简单查询，确认数据库可用
        3.标记仓库已经完成初始化
        """
        try:
            Base.metadata.create_all(self._engine)  # 创建所有继承了Base类的表
            with self._engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise AuditUnavailableError("Audit database initialization failed") from exc
        self._initialized = True

    def record(self, event: AuditEvent) -> None:
        """Append one event after recursive redaction."""
        """
        1.记录审计记录，目前包括：
            plan.reviewed:计划已审查
            confirmation.resolved:用户批准或拒绝计划
            tool.started:工具开始执行
            tool.completed:工具执行并验证成功
            tool.failed:工具执行或验证失败
        2.记录在保存前先执行_to_row(),_to_row()会对敏感的数据执行_redact_mapping()方法，进行脱敏
        """
        if not self._initialized:
            raise AuditUnavailableError("Audit repository is not initialized")
        row = self._to_row(event)
        try:
            with self._sessions.begin() as session:
                session.add(row)
        except SQLAlchemyError as exc:
            raise AuditUnavailableError("Audit event write failed") from exc

    def list_recent(self, limit: int = 100) -> tuple[AuditEventRow, ...]:
        """Return newest events with a strict upper bound."""
        """
        1.查询审计页面最近的记录
        """
        if not self._initialized:
            raise AuditUnavailableError("Audit repository is not initialized")
        bounded_limit = max(1, min(limit, 500))
        try:
            with Session(self._engine) as session:
                statement = (
                    select(AuditEventRow)
                    .order_by(AuditEventRow.occurred_at.desc())
                    .limit(bounded_limit)
                )
                return tuple(session.scalars(statement))
        except SQLAlchemyError as exc:
            raise AuditUnavailableError("Audit query failed") from exc

    def close(self) -> None:
        """Release pooled SQLite connections deterministically."""
        self._engine.dispose()  # 清除所有连接
        self._initialized = False

    @staticmethod
    def _redact_mapping(value: dict[str, JsonValue] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        redacted = redact_json(value)  # 判断 value 是否含敏感 key; 如有则替换对应值
        if not isinstance(redacted, dict):
            raise TypeError("Redacted audit mapping changed shape")
        return redacted

    @classmethod
    def _to_row(cls, event: AuditEvent) -> AuditEventRow:
        return AuditEventRow(
            event_id=str(event.event_id),
            occurred_at=event.occurred_at,
            event_type=event.event_type,
            original_request=redact_text(event.original_request)
            if event.original_request
            else None,
            plan=cls._redact_mapping(event.plan),
            plan_id=event.plan_id,
            plan_version=event.plan_version,
            agent_decision=redact_text(event.agent_decision) if event.agent_decision else None,
            step_id=event.step_id,
            tool_name=event.tool_name,
            parameters=cls._redact_mapping(event.parameters),
            risk_level=event.risk_level.value if event.risk_level else None,
            confirmation_required=event.confirmation_required,
            confirmation_result=event.confirmation_result,
            before_state=cls._redact_mapping(event.before_state),
            result=cls._redact_mapping(event.result),
            after_state=cls._redact_mapping(event.after_state),
            error=cls._redact_mapping(event.error),
            rollback=cls._redact_mapping(event.rollback),
            verification=cls._redact_mapping(event.verification),
            model_provider=event.model_provider,
            model_request_id=event.model_request_id,
            app_version=event.app_version,
            git_commit=event.git_commit,
            duration_ms=event.duration_ms,
        )
