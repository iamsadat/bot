"""Tests for the pluggable vector store (in-memory + pgvector backends).

Fully offline: no real Postgres, no network. ``PgVectorStore`` is exercised
against a hand-rolled ``FakeConnection``/``FakeCursor`` (mirroring the
``FakeHTTPClient`` testing philosophy in ``jobhunt.http``: records every
call, returns deterministic canned data) so the SQL text and parameter
shapes are asserted directly.
"""

from __future__ import annotations

from typing import Any

import pytest

from jobhunt.embeddings import embed_jd_text, embed_user_skills
from jobhunt.vectorstore import (
    InMemoryVectorStore,
    PgVectorStore,
    SearchHit,
    VectorRecord,
    VectorStore,
    VectorStoreError,
    cosine_similarity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bump(vector: list[float], index: int, amount: float = 1.0) -> list[float]:
    """Return a copy of ``vector`` with ``amount`` added at ``index``.

    Used to derive distinguishable-but-related vectors from the project's
    real (currently placeholder, all-zero) embedding functions so ranking
    behaviour can be exercised deterministically and offline.
    """
    out = list(vector)
    out[index] += amount
    return out


# ---------------------------------------------------------------------------
# base.py: cosine_similarity helper
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert abs(cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) - 1.0) < 1e-9

    def test_orthogonal_vectors(self):
        assert abs(cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-9

    def test_opposite_vectors(self):
        assert abs(cosine_similarity([1.0, 0.0], [-1.0, 0.0]) - (-1.0)) < 1e-9

    def test_empty_vectors_return_zero(self):
        assert cosine_similarity([], [1.0]) == 0.0
        assert cosine_similarity([1.0], []) == 0.0
        assert cosine_similarity([], []) == 0.0

    def test_mismatched_length_returns_zero(self):
        assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0

    def test_zero_magnitude_returns_zero(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


# ---------------------------------------------------------------------------
# InMemoryVectorStore
# ---------------------------------------------------------------------------


class TestInMemoryVectorStore:
    def test_implements_vectorstore_protocol(self):
        assert isinstance(InMemoryVectorStore(), VectorStore)

    def test_add_and_count(self):
        store = InMemoryVectorStore()
        assert store.count() == 0
        store.add([
            VectorRecord(id="a", vector=[1.0, 0.0]),
            VectorRecord(id="b", vector=[0.0, 1.0]),
        ])
        assert store.count() == 2

    def test_add_is_upsert(self):
        store = InMemoryVectorStore()
        store.add([VectorRecord(id="a", vector=[1.0, 0.0], text="first")])
        store.add([VectorRecord(id="a", vector=[0.0, 1.0], text="second")])
        assert store.count() == 1
        hits = store.search([0.0, 1.0], k=1)
        assert hits[0].text == "second"

    def test_search_returns_search_hit_instances(self):
        store = InMemoryVectorStore()
        store.add([VectorRecord(id="a", vector=[1.0, 0.0], metadata={"k": "v"}, text="hello")])
        hits = store.search([1.0, 0.0], k=1)
        assert len(hits) == 1
        assert isinstance(hits[0], SearchHit)
        assert hits[0].id == "a"
        assert hits[0].metadata == {"k": "v"}
        assert hits[0].text == "hello"

    def test_search_ranks_nearest_first(self):
        store = InMemoryVectorStore()
        store.add([
            VectorRecord(id="close", vector=[1.0, 0.0]),
            VectorRecord(id="mid", vector=[1.0, 1.0]),
            VectorRecord(id="far", vector=[0.0, 1.0]),
        ])
        hits = store.search([1.0, 0.0], k=3)
        assert [h.id for h in hits] == ["close", "mid", "far"]
        # scores should be non-increasing
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)

    def test_search_respects_k_limit(self):
        store = InMemoryVectorStore()
        store.add([VectorRecord(id=str(i), vector=[float(i), 1.0]) for i in range(10)])
        hits = store.search([5.0, 1.0], k=3)
        assert len(hits) == 3
        # k larger than the corpus returns everything, no error.
        assert len(store.search([5.0, 1.0], k=50)) == 10

    def test_search_empty_store_returns_empty(self):
        store = InMemoryVectorStore()
        assert store.search([1.0, 0.0]) == []

    def test_search_default_k_is_five(self):
        store = InMemoryVectorStore()
        store.add([VectorRecord(id=str(i), vector=[1.0, float(i)]) for i in range(10)])
        hits = store.search([1.0, 0.0])
        assert len(hits) == 5

    def test_metadata_filter_matches_subset(self):
        store = InMemoryVectorStore()
        store.add([
            VectorRecord(id="py", vector=[1.0, 0.0], metadata={"lang": "python"}),
            VectorRecord(id="go", vector=[1.0, 0.0], metadata={"lang": "go"}),
        ])
        hits = store.search([1.0, 0.0], k=10, filter={"lang": "python"})
        assert [h.id for h in hits] == ["py"]

    def test_metadata_filter_multiple_keys_requires_all(self):
        store = InMemoryVectorStore()
        store.add([
            VectorRecord(id="a", vector=[1.0, 0.0], metadata={"lang": "python", "level": "senior"}),
            VectorRecord(id="b", vector=[1.0, 0.0], metadata={"lang": "python", "level": "junior"}),
        ])
        hits = store.search([1.0, 0.0], k=10, filter={"lang": "python", "level": "senior"})
        assert [h.id for h in hits] == ["a"]

    def test_metadata_filter_no_match_returns_empty(self):
        store = InMemoryVectorStore()
        store.add([VectorRecord(id="a", vector=[1.0, 0.0], metadata={"lang": "python"})])
        hits = store.search([1.0, 0.0], filter={"lang": "rust"})
        assert hits == []

    def test_delete_removes_records(self):
        store = InMemoryVectorStore()
        store.add([VectorRecord(id="a", vector=[1.0]), VectorRecord(id="b", vector=[2.0])])
        store.delete(["a"])
        assert store.count() == 1
        assert store.search([1.0], k=10)[0].id == "b"

    def test_delete_unknown_id_is_noop_and_supports_multiple_ids(self):
        store = InMemoryVectorStore()
        store.add([VectorRecord(id=str(i), vector=[float(i)]) for i in range(5)])
        store.delete(["does-not-exist", "0", "1", "2"])
        assert store.count() == 2


class TestInMemoryVectorStoreWithRealEmbeddings:
    """Exercise the store with vectors produced by jobhunt.embeddings."""

    def test_embed_jd_text_shape_is_indexable(self):
        vector = embed_jd_text("Senior Backend Engineer, Python, Kubernetes")
        record = VectorRecord(id="jd-1", vector=vector, text="Senior Backend Engineer")
        store = InMemoryVectorStore()
        store.add([record])
        assert store.count() == 1

    def test_embed_user_skills_shape_is_indexable(self):
        vector = embed_user_skills(["python", "kubernetes", "postgresql"])
        store = InMemoryVectorStore()
        store.add([VectorRecord(id="profile-1", vector=vector)])
        assert store.count() == 1

    def test_similar_jd_text_ranks_above_dissimilar(self):
        """Two JD embeddings nudged the same way should rank above one nudged differently.

        ``embed_jd_text`` is currently a Phase-2 placeholder returning a
        fixed-dimension zero vector for every input, so plain embeddings
        carry no signal yet. We still route everything through the real
        project function (to pin its dimensionality/contract) and layer a
        deterministic, small perturbation on top to simulate two JDs being
        semantically "close" vs. "far" — this is exactly the ranking
        behaviour the store must get right once real embeddings land.
        """
        base_a = embed_jd_text("Senior Backend Engineer, Python, distributed systems")
        base_b = embed_jd_text("Backend Engineer, Python, microservices")
        base_c = embed_jd_text("Senior Product Designer, Figma, user research")
        assert len(base_a) == len(base_b) == len(base_c)

        # Two "Python backend" JDs nudged in the same direction (similar).
        vec_a = _bump(_bump(base_a, 0, 3.0), 1, 2.0)
        vec_b = _bump(_bump(base_b, 0, 3.0), 1, 2.0)
        # A "design" JD nudged in an unrelated direction (dissimilar).
        vec_c = _bump(_bump(base_c, 2, -3.0), 3, -2.0)

        store = InMemoryVectorStore()
        store.add([
            VectorRecord(id="backend-senior", vector=vec_a, text="Senior Backend Engineer"),
            VectorRecord(id="backend-mid", vector=vec_b, text="Backend Engineer"),
            VectorRecord(id="designer", vector=vec_c, text="Senior Product Designer"),
        ])

        hits = store.search(vec_a, k=3)
        ranked_ids = [h.id for h in hits]
        assert ranked_ids[0] == "backend-senior"
        assert ranked_ids[1] == "backend-mid"
        assert ranked_ids[2] == "designer"
        assert hits[0].score >= hits[1].score >= hits[2].score


# ---------------------------------------------------------------------------
# Fakes for PgVectorStore (DB-API 2.0: cursor()/execute()/fetchall()/commit())
# ---------------------------------------------------------------------------


class FakeCursor:
    """Records every executed (sql, params) pair; returns canned rows."""

    def __init__(self, connection: "FakeConnection") -> None:
        self._connection = connection
        self._rows: list[Any] = []

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self._connection.calls.append((sql, params))
        self._rows = self._connection.next_fetchall

    def fetchall(self) -> list[Any]:
        return self._rows


class FakeConnection:
    """In-memory DB-API 2.0 connection double for PgVectorStore tests.

    Mirrors ``jobhunt.http.FakeHTTPClient``'s philosophy: deterministic,
    records every call so tests assert exact SQL/param shapes, and lets the
    test pre-load canned ``fetchall()`` rows for the next ``execute()``.
    """

    def __init__(self, fetchall_rows: list[Any] | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...] | None]] = []
        self.commit_count = 0
        self.next_fetchall: list[Any] = fetchall_rows if fetchall_rows is not None else []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commit_count += 1


# ---------------------------------------------------------------------------
# PgVectorStore
# ---------------------------------------------------------------------------


class TestPgVectorStoreSchema:
    def test_implements_vectorstore_protocol(self):
        assert isinstance(PgVectorStore(FakeConnection()), VectorStore)

    def test_ensure_schema_creates_extension(self):
        conn = FakeConnection()
        PgVectorStore(conn).ensure_schema()
        sqls = [sql for sql, _ in conn.calls]
        assert any("CREATE EXTENSION IF NOT EXISTS vector" in sql for sql in sqls)

    def test_ensure_schema_creates_table_with_dimension(self):
        conn = FakeConnection()
        PgVectorStore(conn, table="my_vectors", dimension=384).ensure_schema()
        sqls = [sql for sql, _ in conn.calls]
        table_sql = next(sql for sql in sqls if "CREATE TABLE" in sql)
        assert "my_vectors" in table_sql
        assert "vector(384)" in table_sql
        assert "id text primary key" in table_sql
        assert "metadata jsonb" in table_sql

    def test_ensure_schema_creates_similarity_index(self):
        conn = FakeConnection()
        PgVectorStore(conn, table="my_vectors").ensure_schema()
        sqls = [sql for sql, _ in conn.calls]
        index_sql = next(sql for sql in sqls if "CREATE INDEX" in sql)
        assert "ivfflat" in index_sql or "hnsw" in index_sql
        assert "my_vectors" in index_sql

    def test_ensure_schema_commits(self):
        conn = FakeConnection()
        PgVectorStore(conn).ensure_schema()
        assert conn.commit_count == 1

    def test_ensure_schema_statement_order(self):
        conn = FakeConnection()
        PgVectorStore(conn).ensure_schema()
        sqls = [sql for sql, _ in conn.calls]
        assert "CREATE EXTENSION" in sqls[0]
        assert "CREATE TABLE" in sqls[1]
        assert "CREATE INDEX" in sqls[2]


class TestPgVectorStoreAdd:
    def test_add_emits_insert_with_pgvector_literal(self):
        conn = FakeConnection()
        store = PgVectorStore(conn, table="jobhunt_vectors", dimension=3)
        store.add([VectorRecord(id="jd-1", vector=[1.0, 2.5, -3.0], metadata={"src": "greenhouse"}, text="hi")])

        assert len(conn.calls) == 1
        sql, params = conn.calls[0]
        assert "INSERT INTO jobhunt_vectors" in sql
        assert "ON CONFLICT (id) DO UPDATE" in sql
        assert params[0] == "jd-1"
        assert params[1] == "[1.0,2.5,-3.0]"
        assert params[2] == '{"src": "greenhouse"}'
        assert params[3] == "hi"

    def test_add_multiple_records_executes_once_each(self):
        conn = FakeConnection()
        store = PgVectorStore(conn)
        store.add([
            VectorRecord(id="a", vector=[1.0, 0.0]),
            VectorRecord(id="b", vector=[0.0, 1.0]),
        ])
        assert len(conn.calls) == 2
        ids = [params[0] for _, params in conn.calls]
        assert ids == ["a", "b"]

    def test_add_commits(self):
        conn = FakeConnection()
        store = PgVectorStore(conn)
        store.add([VectorRecord(id="a", vector=[1.0])])
        assert conn.commit_count == 1

    def test_add_empty_list_is_noop(self):
        conn = FakeConnection()
        store = PgVectorStore(conn)
        store.add([])
        assert conn.calls == []
        assert conn.commit_count == 0

    def test_add_default_metadata_and_text_serialize_to_empty(self):
        conn = FakeConnection()
        store = PgVectorStore(conn)
        store.add([VectorRecord(id="a", vector=[1.0])])
        _, params = conn.calls[0]
        assert params[2] == "{}"
        assert params[3] == ""


class TestPgVectorStoreSearch:
    def test_search_emits_order_by_distance_with_limit(self):
        conn = FakeConnection(fetchall_rows=[])
        store = PgVectorStore(conn, table="jobhunt_vectors")
        store.search([1.0, 0.0], k=7)

        assert len(conn.calls) == 1
        sql, params = conn.calls[0]
        assert "<=>" in sql
        assert "ORDER BY embedding <=> %s" in sql
        assert "LIMIT %s" in sql
        assert "jobhunt_vectors" in sql
        # vector literal appears twice (select distance + order by), then k.
        assert params[0] == "[1.0,0.0]"
        assert params[-2] == "[1.0,0.0]"
        assert params[-1] == 7

    def test_search_default_k_is_five(self):
        conn = FakeConnection(fetchall_rows=[])
        store = PgVectorStore(conn)
        store.search([1.0])
        _, params = conn.calls[0]
        assert params[-1] == 5

    def test_search_parses_fetched_rows_into_search_hits(self):
        rows = [
            ("a", 0.1, {"lang": "python"}, "Job A"),
            ("b", 0.4, {"lang": "go"}, "Job B"),
        ]
        conn = FakeConnection(fetchall_rows=rows)
        store = PgVectorStore(conn)
        hits = store.search([1.0, 0.0], k=2)

        assert len(hits) == 2
        assert all(isinstance(h, SearchHit) for h in hits)
        assert hits[0].id == "a"
        assert hits[0].metadata == {"lang": "python"}
        assert hits[0].text == "Job A"
        # score = 1 - distance
        assert abs(hits[0].score - 0.9) < 1e-9
        assert abs(hits[1].score - 0.6) < 1e-9

    def test_search_parses_json_text_metadata(self):
        rows = [("a", 0.2, '{"lang": "python"}', "Job A")]
        conn = FakeConnection(fetchall_rows=rows)
        store = PgVectorStore(conn)
        hits = store.search([1.0], k=1)
        assert hits[0].metadata == {"lang": "python"}

    def test_search_handles_null_metadata_and_text(self):
        rows = [("a", 0.0, None, None)]
        conn = FakeConnection(fetchall_rows=rows)
        store = PgVectorStore(conn)
        hits = store.search([1.0], k=1)
        assert hits[0].metadata == {}
        assert hits[0].text == ""

    def test_search_invalid_metadata_json_raises_vectorstore_error(self):
        rows = [("a", 0.0, "{not valid json", "")]
        conn = FakeConnection(fetchall_rows=rows)
        store = PgVectorStore(conn)
        with pytest.raises(VectorStoreError):
            store.search([1.0], k=1)

    def test_search_with_metadata_filter_adds_containment_clause(self):
        conn = FakeConnection(fetchall_rows=[])
        store = PgVectorStore(conn)
        store.search([1.0, 0.0], k=3, filter={"lang": "python"})

        sql, params = conn.calls[0]
        assert "metadata @> %s::jsonb" in sql
        assert "WHERE" in sql
        # filter param sits between the distance-select vector and the
        # order-by vector / limit.
        assert params[1] == '{"lang": "python"}'
        assert params[-1] == 3

    def test_search_without_filter_omits_where_clause(self):
        conn = FakeConnection(fetchall_rows=[])
        store = PgVectorStore(conn)
        store.search([1.0], k=1)
        sql, _ = conn.calls[0]
        assert "WHERE" not in sql

    def test_search_empty_results(self):
        conn = FakeConnection(fetchall_rows=[])
        store = PgVectorStore(conn)
        assert store.search([1.0], k=5) == []


class TestPgVectorStoreDeleteAndCount:
    def test_delete_emits_delete_with_id_list(self):
        conn = FakeConnection()
        store = PgVectorStore(conn, table="jobhunt_vectors")
        store.delete(["a", "b", "c"])

        sql, params = conn.calls[0]
        assert "DELETE FROM jobhunt_vectors" in sql
        assert "WHERE id IN" in sql
        assert params == ("a", "b", "c")

    def test_delete_commits(self):
        conn = FakeConnection()
        store = PgVectorStore(conn)
        store.delete(["a"])
        assert conn.commit_count == 1

    def test_delete_empty_list_is_noop(self):
        conn = FakeConnection()
        store = PgVectorStore(conn)
        store.delete([])
        assert conn.calls == []
        assert conn.commit_count == 0

    def test_count_emits_count_query_and_parses_result(self):
        conn = FakeConnection(fetchall_rows=[(42,)])
        store = PgVectorStore(conn, table="jobhunt_vectors")
        assert store.count() == 42
        sql, _ = conn.calls[0]
        assert "SELECT COUNT(*)" in sql
        assert "jobhunt_vectors" in sql

    def test_count_no_rows_returns_zero_and_does_not_commit(self):
        conn = FakeConnection(fetchall_rows=[])
        store = PgVectorStore(conn)
        assert store.count() == 0
        assert conn.commit_count == 0


class TestPgVectorStoreDoesNotImportPsycopg2:
    def test_module_has_no_top_level_psycopg2_dependency(self):
        import jobhunt.vectorstore.pgvector as module
        import inspect

        source = inspect.getsource(module)
        assert "import psycopg2" not in source
