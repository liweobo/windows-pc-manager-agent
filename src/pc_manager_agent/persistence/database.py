"""SQLite engine construction with explicit local paths."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL


def create_sqlite_engine(database_path: Path) -> Engine:
    """Create a local SQLite engine with integrity-preserving pragmas."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    url = URL.create("sqlite+pysqlite", database=str(database_path))
    engine = create_engine(url, future=True)

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=FULL")
        except Exception:
            # SQLAlchemy may discard a connection that fails in a connect event
            # without explicitly closing it. Python 3.13 correctly warns about
            # that leaked SQLite handle, so close before preserving the failure.
            dbapi_connection.close()  # type: ignore[attr-defined]
            raise
        finally:
            cursor.close()

    return engine
