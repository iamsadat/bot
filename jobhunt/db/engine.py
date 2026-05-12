"""Database engine and session factory.

Configurable to use Postgres in production, SQLite in-memory for dev/tests.
Provides context manager for transaction safety.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool, NullPool

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def create_engine(
    url: str | None = None,
    echo: bool = False,
) -> Engine:
    """Create SQLAlchemy engine (Postgres in prod, SQLite for dev).

    Args:
        url: Connection string. If None, uses DATABASE_URL env var or in-memory SQLite.
        echo: Enable SQL logging.

    Returns:
        SQLAlchemy Engine instance.
    """
    global _engine, _SessionLocal

    if _engine is not None:
        return _engine

    if url is None:
        url = os.getenv("DATABASE_URL", "sqlite:///:memory:")

    # SQLite in-memory uses StaticPool so conn isn't closed after each use.
    # Postgres uses NullPool (close after each).
    if "sqlite:///:memory:" in url or "sqlite://" in url and ":memory:" in url:
        pool = StaticPool
        kwargs = {"connect_args": {"check_same_thread": False}}
    else:
        pool = NullPool
        kwargs = {"pool_pre_ping": True}  # Verify conn before use.

    _engine = sa_create_engine(url, echo=echo, poolclass=pool, **kwargs)

    # Foreign key constraints on SQLite (off by default).
    if "sqlite" in url:
        @event.listens_for(Engine, "connect")
        def set_sqlite_pragma(dbapi_conn, _):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_session() -> Session:
    """Get a new database session.

    Must call this within a context manager or manually close().
    """
    if _SessionLocal is None:
        create_engine()
    return _SessionLocal()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Provide a transactional scope around a series of operations.

    Example:
        with session_scope() as session:
            user = session.query(User).get("u-123")
    """
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
