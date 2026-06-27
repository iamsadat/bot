"""In-memory vector store: the default offline backend.

Dict-backed, stdlib-only, deterministic. Serves as both the development /
test default and the reference implementation that ``PgVectorStore``'s
behaviour is checked against conceptually (same ranking semantics, same
filter semantics).
"""

from __future__ import annotations

from typing import Any

from jobhunt.vectorstore.base import SearchHit, VectorRecord, cosine_similarity


class InMemoryVectorStore:
    """Dict-backed :class:`~jobhunt.vectorstore.base.VectorStore` implementation."""

    def __init__(self) -> None:
        self._records: dict[str, VectorRecord] = {}

    def add(self, records: list[VectorRecord]) -> None:
        """Insert or upsert ``records``, keyed by ``VectorRecord.id``."""
        for record in records:
            self._records[record.id] = record

    def search(
        self,
        query_vector: list[float],
        k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        """Return the ``k`` nearest records by cosine similarity, best first.

        ``filter`` (if given) keeps only records whose metadata contains
        every key/value pair in ``filter`` (equality match).
        """
        candidates = self._records.values()
        if filter:
            candidates = [r for r in candidates if _matches(r.metadata, filter)]

        scored = [
            SearchHit(id=r.id, score=cosine_similarity(query_vector, r.vector), metadata=r.metadata, text=r.text)
            for r in candidates
        ]
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[:k]

    def delete(self, ids: list[str]) -> None:
        """Remove records by id. Unknown ids are ignored."""
        for record_id in ids:
            self._records.pop(record_id, None)

    def count(self) -> int:
        """Return the number of records currently stored."""
        return len(self._records)


def _matches(metadata: dict[str, Any], filter: dict[str, Any]) -> bool:
    return all(metadata.get(key) == value for key, value in filter.items())
