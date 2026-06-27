"""pgvector-backed vector store.

Talks to Postgres + the ``pgvector`` extension through an injected DB-API
2.0 connection (``.cursor()`` / ``cursor.execute(sql, params)`` /
``cursor.fetchall()`` / ``.commit()``). This module never imports a driver
(e.g. ``psycopg2``) itself — bringing the real driver is a runtime/deployment
concern for the caller, which keeps this module importable (and the SQL
shapes unit-testable) without Postgres installed.

Tests exercise this class against a hand-rolled fake connection/cursor
(defined in ``tests/jobhunt/test_vectorstore.py``) that records every
``(sql, params)`` pair and returns canned ``fetchall()`` rows, so the SQL
text and parameter shapes are verified directly rather than against a live
database.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from jobhunt.vectorstore.base import SearchHit, VectorRecord, VectorStoreError


class DBCursor(Protocol):
    def execute(self, sql: str, params: tuple[Any, ...] | None = ...) -> Any: ...
    def fetchall(self) -> list[Any]: ...


class DBConnection(Protocol):
    def cursor(self) -> DBCursor: ...
    def commit(self) -> None: ...


def _vector_literal(vector: list[float]) -> str:
    """Format a vector as a pgvector literal: ``'[v1,v2,...]'``."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


class PgVectorStore:
    """:class:`~jobhunt.vectorstore.base.VectorStore` backed by Postgres + pgvector.

    Parameters
    ----------
    connection:
        Any DB-API 2.0 connection (real ``psycopg2`` connection, or a fake
        for tests). Never imported or constructed by this module.
    table:
        Name of the table to store records in.
    dimension:
        Dimensionality ``N`` of the vectors, used by :meth:`ensure_schema`.
    """

    def __init__(self, connection: DBConnection, *, table: str = "jobhunt_vectors", dimension: int = 1024) -> None:
        self._conn = connection
        self.table = table
        self.dimension = dimension

    # ------------------------------------------------------------------ schema

    def ensure_schema(self) -> None:
        """Create the pgvector extension, table, and similarity index if absent."""
        cur = self._conn.cursor()
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute(
            f"CREATE TABLE IF NOT EXISTS {self.table} ("
            "id text primary key, "
            f"embedding vector({self.dimension}), "
            "metadata jsonb, "
            "text text"
            ")"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS {self.table}_embedding_idx ON {self.table} "
            "USING ivfflat (embedding vector_cosine_ops)"
        )
        self._conn.commit()

    # --------------------------------------------------------------------- add

    def add(self, records: list[VectorRecord]) -> None:
        """Upsert ``records``, formatting each vector as a pgvector literal."""
        if not records:
            return
        cur = self._conn.cursor()
        sql = (
            f"INSERT INTO {self.table} (id, embedding, metadata, text) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET "
            "embedding = EXCLUDED.embedding, metadata = EXCLUDED.metadata, text = EXCLUDED.text"
        )
        for record in records:
            params = (
                record.id,
                _vector_literal(record.vector),
                json.dumps(record.metadata),
                record.text,
            )
            cur.execute(sql, params)
        self._conn.commit()

    # ------------------------------------------------------------------ search

    def search(
        self,
        query_vector: list[float],
        k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        """Return the ``k`` nearest rows ordered by cosine distance (``<=>``).

        ``filter`` (if given) is applied as a JSONB containment clause
        (``metadata @> %s::jsonb``) so the database does the filtering.
        """
        cur = self._conn.cursor()
        vector_literal = _vector_literal(query_vector)
        sql = f"SELECT id, embedding <=> %s AS distance, metadata, text FROM {self.table}"
        params: list[Any] = [vector_literal]

        if filter:
            sql += " WHERE metadata @> %s::jsonb"
            params.append(json.dumps(filter))

        sql += " ORDER BY embedding <=> %s LIMIT %s"
        params.append(vector_literal)
        params.append(k)

        cur.execute(sql, tuple(params))
        rows = cur.fetchall()

        hits: list[SearchHit] = []
        for row in rows:
            row_id, distance, metadata, text = row[0], row[1], row[2], row[3]
            metadata = _coerce_metadata(metadata)
            hits.append(
                SearchHit(
                    id=row_id,
                    score=1.0 - float(distance),
                    metadata=metadata,
                    text=text or "",
                )
            )
        return hits

    # ------------------------------------------------------------------ delete

    def delete(self, ids: list[str]) -> None:
        """Remove rows by id. No-op for an empty ``ids`` list."""
        if not ids:
            return
        cur = self._conn.cursor()
        placeholders = ", ".join(["%s"] * len(ids))
        sql = f"DELETE FROM {self.table} WHERE id IN ({placeholders})"
        cur.execute(sql, tuple(ids))
        self._conn.commit()

    # ------------------------------------------------------------------- count

    def count(self) -> int:
        """Return the number of rows currently stored."""
        cur = self._conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {self.table}")
        rows = cur.fetchall()
        if not rows:
            return 0
        first = rows[0]
        # Support both tuple-like rows and dict-like (RealDictCursor) rows.
        if isinstance(first, dict):
            return int(next(iter(first.values())))
        return int(first[0])


def _coerce_metadata(metadata: Any) -> dict[str, Any]:
    """Normalize a metadata cell that may already be a dict or still JSON text."""
    if metadata is None:
        return {}
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str):
        try:
            return json.loads(metadata)
        except json.JSONDecodeError as exc:
            raise VectorStoreError(f"could not decode metadata JSON: {exc}") from exc
    raise VectorStoreError(f"unexpected metadata type: {type(metadata)!r}")
