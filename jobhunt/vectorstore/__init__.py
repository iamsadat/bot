"""Pluggable vector store for JD/embedding similarity search.

``InMemoryVectorStore`` is the default offline backend (stdlib-only, dict
backed). ``PgVectorStore`` is the production backend, talking to Postgres +
pgvector through an injectable DB-API 2.0 connection so it stays unit
testable without a real database.
"""

from __future__ import annotations

from jobhunt.vectorstore.base import (
    SearchHit,
    VectorRecord,
    VectorStore,
    VectorStoreError,
    cosine_similarity,
)
from jobhunt.vectorstore.memory import InMemoryVectorStore
from jobhunt.vectorstore.pgvector import PgVectorStore

__all__ = [
    "SearchHit",
    "VectorRecord",
    "VectorStore",
    "VectorStoreError",
    "cosine_similarity",
    "InMemoryVectorStore",
    "PgVectorStore",
]
