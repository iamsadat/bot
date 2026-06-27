"""Pluggable vector store abstraction for JD/embedding similarity search.

This module defines the storage-agnostic contract (``VectorStore``) plus the
data shapes (``VectorRecord``, ``SearchHit``) shared by every backend. Two
backends ship alongside it:

- :class:`jobhunt.vectorstore.memory.InMemoryVectorStore` — dict-backed,
  stdlib-only, the default offline backend and reference implementation.
- :class:`jobhunt.vectorstore.pgvector.PgVectorStore` — real pgvector-backed
  implementation that takes an injectable DB-API 2.0 connection so it can be
  unit-tested offline with a fake connection.

Vectors are plain ``list[float]``, matching the shape produced by
``jobhunt.embeddings.embed_jd_text`` / ``embed_user_skills``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class VectorStoreError(Exception):
    """Raised for vector-store-level failures (bad input, backend errors)."""


@dataclass
class VectorRecord:
    """A single item to index: an id, its embedding, and optional payload."""

    id: str
    vector: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)
    text: str = ""


@dataclass
class SearchHit:
    """A single search result: id, similarity score, and original payload."""

    id: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    text: str = ""


@runtime_checkable
class VectorStore(Protocol):
    """Storage-agnostic contract implemented by every backend."""

    def add(self, records: list[VectorRecord]) -> None:
        """Insert or upsert ``records`` into the store."""
        ...

    def search(
        self,
        query_vector: list[float],
        k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        """Return the ``k`` nearest records to ``query_vector``, best first.

        ``filter`` is a simple metadata equality filter: a record matches
        when every key in ``filter`` is present in the record's metadata
        with an equal value.
        """
        ...

    def delete(self, ids: list[str]) -> None:
        """Remove records by id. Unknown ids are ignored."""
        ...

    def count(self) -> int:
        """Return the number of records currently stored."""
        ...


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors (stdlib math only).

    Returns 0.0 for empty vectors, mismatched lengths, or zero-magnitude
    vectors (rather than raising), matching the permissive style of
    ``jobhunt.embeddings.cosine_similarity``.
    """
    if not a or not b or len(a) != len(b):
        return 0.0

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot / (norm_a * norm_b)
