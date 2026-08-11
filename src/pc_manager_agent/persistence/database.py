"""SQLite engine construction with explicit local paths."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL


# 创建数据库引擎
def create_sqlite_engine(database_path: Path) -> Engine:
    """Create a local SQLite engine with integrity-preserving pragmas."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    url = URL.create("sqlite+pysqlite", database=str(database_path))
    engine = create_engine(url, future=True)

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")  # 开启 SQLite 的外键约束检查
            cursor.execute("PRAGMA journal_mode=WAL")  # 开启 WAL (Write-Ahead Logging) 模式
            cursor.execute(
                "PRAGMA synchronous=FULL"
            )  # 确保写操作在系统崩溃时也能最大限度保证数据完整性
        except Exception:
            # SQLAlchemy may discard a connection that fails in a connect event
            # without explicitly closing it. Python 3.13 correctly warns about
            # that leaked SQLite handle, so close before preserving the failure.
            dbapi_connection.close()  # type: ignore[attr-defined]
            raise
        finally:
            cursor.close()

    return engine
