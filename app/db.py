"""SQLite engine + session factory."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


_engine = create_engine(
    f"sqlite:///{settings.db_path}",
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)
    Base.metadata.create_all(_engine)
    _migrate_columns()


def _migrate_columns() -> None:
    """Add columns introduced after a table already exists.

    ``create_all`` only creates missing tables, never missing columns on a
    table that's already there. There's no migration framework in this repo
    (ponytail: guarded ALTER TABLE; move to alembic if more than a couple of
    these accumulate), so new nullable columns are patched in here.
    """
    additions = {
        "strategy_state": [("market", "JSON"), ("narrative", "VARCHAR(512)")],
    }
    with _engine.connect() as conn:
        for table, columns in additions.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, coltype in columns:
                if name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}")
        conn.commit()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
