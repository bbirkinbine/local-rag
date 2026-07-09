"""Tests for local_rag.store — LanceDB-backed chunk store with hybrid search."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_rag.models import Chunk, SearchHit
from local_rag.store import Store

DIM = 8


def _chunk(
    *,
    path: str = "/doc.md",
    idx: int = 0,
    text: str = "hello world",
    vector: list[float] | None = None,
    heading: str = "",
    file_hash: str = "h0",
    char_start: int = 0,
    char_end: int = 11,
) -> Chunk:
    if vector is None:
        vector = [1.0] + [0.0] * (DIM - 1)
    return Chunk(
        source_path=path,
        file_hash=file_hash,
        chunk_index=idx,
        char_start=char_start,
        char_end=char_end,
        heading_path=heading,
        text=text,
        vector=vector,
    )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "db", vector_dim=DIM)


# ----------------------------------------------------------------- models ---


def test_chunk_id_derives_from_source_path_and_index() -> None:
    c = _chunk(path="/foo/bar.md", idx=3)
    assert c.id == "/foo/bar.md::3"


# ----------------------------------------------------------------- table ---


def test_ensure_table_creates_table(store: Store) -> None:
    store.ensure_table("vault")
    assert "vault" in store.list_sources()


def test_ensure_table_is_idempotent(store: Store) -> None:
    store.ensure_table("vault")
    store.ensure_table("vault")  # no error
    assert store.list_sources() == ["vault"]


def test_list_sources_returns_alphabetical(store: Store) -> None:
    store.ensure_table("zeta")
    store.ensure_table("alpha")
    store.ensure_table("mid")
    assert store.list_sources() == ["alpha", "mid", "zeta"]


# ----------------------------------------------------------------- upsert ---


def test_upsert_persists_rows(store: Store) -> None:
    store.ensure_table("vault")
    store.upsert_chunks("vault", [_chunk(path="/a.md", idx=0, text="hello")])

    counts = store.chunk_counts()

    assert counts == {"vault": 1}


def test_upsert_empty_list_is_noop(store: Store) -> None:
    store.ensure_table("vault")
    store.upsert_chunks("vault", [])

    assert store.chunk_counts() == {"vault": 0}


def test_upsert_raises_on_vector_dim_mismatch(store: Store) -> None:
    store.ensure_table("vault")
    bad = _chunk(path="/a.md", idx=0, text="x", vector=[1.0, 0.0])  # 2-dim, expected 8

    with pytest.raises(ValueError, match="dim mismatch"):
        store.upsert_chunks("vault", [bad])


def test_upsert_replaces_existing_chunk(store: Store) -> None:
    store.ensure_table("vault")
    store.upsert_chunks("vault", [_chunk(path="/a.md", idx=0, text="original")])
    store.upsert_chunks(
        "vault",
        [_chunk(path="/a.md", idx=0, text="updated", file_hash="h1")],
    )

    # Still one row; the FTS lookup recovers the new text.
    assert store.chunk_counts() == {"vault": 1}
    hits = store.search_hybrid(
        ["vault"],
        query_text="updated",
        query_vector=[1.0] + [0.0] * (DIM - 1),
        k=5,
    )
    assert any(h.chunk.text == "updated" for h in hits)
    assert not any(h.chunk.text == "original" for h in hits)


# ----------------------------------------------------------------- delete ---


def test_delete_file_removes_only_matching_rows(store: Store) -> None:
    store.ensure_table("vault")
    store.upsert_chunks(
        "vault",
        [
            _chunk(path="/a.md", idx=0, text="alpha"),
            _chunk(path="/a.md", idx=1, text="alpha two"),
            _chunk(path="/b.md", idx=0, text="beta"),
        ],
    )

    removed = store.delete_file("vault", "/a.md")

    assert removed == 2
    assert store.chunk_counts() == {"vault": 1}


def test_delete_file_handles_paths_with_quotes(store: Store) -> None:
    store.ensure_table("vault")
    weird = "/with'quote.md"
    store.upsert_chunks("vault", [_chunk(path=weird, idx=0, text="x")])

    removed = store.delete_file("vault", weird)

    assert removed == 1
    assert store.chunk_counts() == {"vault": 0}


def test_chunk_counts_reflects_deletes(store: Store) -> None:
    store.ensure_table("vault")
    store.upsert_chunks(
        "vault",
        [_chunk(path="/a.md", idx=i, text=f"t{i}") for i in range(3)],
    )

    store.delete_file("vault", "/a.md")

    assert store.chunk_counts() == {"vault": 0}


# ----------------------------------------------------------- search_vector ---


def test_search_vector_returns_nearest_first(store: Store) -> None:
    store.ensure_table("vault")
    # /x.md is exactly aligned with the query; /y.md is 45° off; /z.md is 90° off.
    # Distinct angles give distinct cosine distances (no ties).
    store.upsert_chunks(
        "vault",
        [
            _chunk(path="/x.md", idx=0, text="x", vector=[1.0, 0, 0, 0, 0, 0, 0, 0]),
            _chunk(path="/y.md", idx=0, text="y", vector=[1.0, 1.0, 0, 0, 0, 0, 0, 0]),
            _chunk(path="/z.md", idx=0, text="z", vector=[0, 1.0, 0, 0, 0, 0, 0, 0]),
        ],
    )

    hits = store.search_vector(["vault"], [1.0, 0, 0, 0, 0, 0, 0, 0], k=3)

    assert [h.chunk.source_path for h in hits] == ["/x.md", "/y.md", "/z.md"]
    assert hits[0].score > hits[1].score > hits[2].score


def test_search_vector_across_multiple_sources(store: Store) -> None:
    store.ensure_table("vault")
    store.ensure_table("code")
    store.upsert_chunks(
        "vault",
        [_chunk(path="/v.md", idx=0, text="v", vector=[1.0, 0, 0, 0, 0, 0, 0, 0])],
    )
    store.upsert_chunks(
        "code",
        [_chunk(path="/c.py", idx=0, text="c", vector=[0.9, 0.1, 0, 0, 0, 0, 0, 0])],
    )

    hits = store.search_vector(["vault", "code"], [1.0, 0, 0, 0, 0, 0, 0, 0], k=2)

    assert len(hits) == 2
    assert hits[0].chunk.source_path == "/v.md"  # exact match wins
    sources = {h.source_name for h in hits}
    assert sources == {"vault", "code"}


def test_search_vector_empty_sources_returns_empty(store: Store) -> None:
    assert store.search_vector([], [1.0] + [0.0] * (DIM - 1), k=5) == []


# ----------------------------------------------------------- search_hybrid ---


def test_search_hybrid_returns_lexical_only_match(store: Store) -> None:
    """A doc with only a keyword hit (no vector similarity) is still found."""
    store.ensure_table("vault")
    store.upsert_chunks(
        "vault",
        [
            _chunk(
                path="/lex.md",
                idx=0,
                text="elasticsearch tantivy bm25",
                vector=[0, 0, 0, 0, 0, 0, 0, 1.0],  # orthogonal to query
            ),
        ],
    )

    hits = store.search_hybrid(
        ["vault"],
        query_text="tantivy",
        query_vector=[1.0, 0, 0, 0, 0, 0, 0, 0],
        k=5,
    )

    assert any(h.chunk.source_path == "/lex.md" for h in hits)


def test_search_hybrid_returns_semantic_only_match(store: Store) -> None:
    """A doc with only a vector hit (no keyword overlap) is still found."""
    store.ensure_table("vault")
    store.upsert_chunks(
        "vault",
        [
            _chunk(
                path="/sem.md",
                idx=0,
                text="nothing in common at all",
                vector=[1.0, 0, 0, 0, 0, 0, 0, 0],  # aligned with query
            ),
        ],
    )

    hits = store.search_hybrid(
        ["vault"],
        query_text="banana",
        query_vector=[1.0, 0, 0, 0, 0, 0, 0, 0],
        k=5,
    )

    assert any(h.chunk.source_path == "/sem.md" for h in hits)


def test_search_hybrid_ranks_double_match_above_single(store: Store) -> None:
    """RRF: a doc that hits both rankers ranks above one that hits only one.

    Inputs are designed so neither ranker has ties (cosine distances and BM25
    scores are all distinct) — keeps RRF ordering deterministic across runs.
    """
    store.ensure_table("vault")
    store.upsert_chunks(
        "vault",
        [
            _chunk(
                path="/both.md",
                idx=0,
                text="tantivy bm25 retrieval",  # all 3 query terms — top FTS hit
                vector=[1.0, 0, 0, 0, 0, 0, 0, 0],  # exact vector match
            ),
            _chunk(
                path="/lex.md",
                idx=0,
                text="tantivy",  # only 1 query term — lower FTS rank
                vector=[0, 0, 0, 0, 0, 0, 0, 1.0],  # orthogonal — bottom vector rank
            ),
            _chunk(
                path="/sem.md",
                idx=0,
                text="orange purple sky",  # no query-term overlap — not in FTS
                vector=[0.9, 0.1, 0, 0, 0, 0, 0, 0],  # close but distinct
            ),
        ],
    )

    hits = store.search_hybrid(
        ["vault"],
        query_text="tantivy bm25 retrieval",
        query_vector=[1.0, 0, 0, 0, 0, 0, 0, 0],
        k=3,
    )

    assert hits[0].chunk.source_path == "/both.md"
    assert {h.chunk.source_path for h in hits} == {"/both.md", "/lex.md", "/sem.md"}


def test_search_hybrid_empty_sources_returns_empty(store: Store) -> None:
    assert (
        store.search_hybrid([], query_text="x", query_vector=[1.0] + [0.0] * (DIM - 1), k=5) == []
    )


# ----------------------------------------------------- result-shape checks ---


def test_search_results_carry_full_chunk_and_source_name(store: Store) -> None:
    store.ensure_table("vault")
    chunk = _chunk(
        path="/a.md",
        idx=2,
        text="payload text",
        vector=[1.0, 0, 0, 0, 0, 0, 0, 0],
        heading="/Section/Sub",
        file_hash="abc",
        char_start=10,
        char_end=22,
    )
    store.upsert_chunks("vault", [chunk])

    hits = store.search_vector(["vault"], [1.0, 0, 0, 0, 0, 0, 0, 0], k=1)

    assert len(hits) == 1
    h = hits[0]
    assert isinstance(h, SearchHit)
    assert h.source_name == "vault"
    assert h.chunk.source_path == "/a.md"
    assert h.chunk.chunk_index == 2
    assert h.chunk.text == "payload text"
    assert h.chunk.heading_path == "/Section/Sub"
    assert h.chunk.file_hash == "abc"
    assert h.chunk.char_start == 10
    assert h.chunk.char_end == 22


# ------------------------------------------------------- score transparency ---
# `score` stays the ranker's fused value (RRF); `cosine` and `bm25` expose
# the raw signals so callers can judge hit strength and threshold.


def test_search_hybrid_exposes_cosine_on_every_hit(store: Store) -> None:
    store.ensure_table("vault")
    near = [1.0] + [0.0] * (DIM - 1)
    far = [0.0, 1.0] + [0.0] * (DIM - 2)
    store.upsert_chunks(
        "vault",
        [
            _chunk(path="/near.md", idx=0, text="unrelated words", vector=near),
            _chunk(path="/far.md", idx=0, text="other content", vector=far),
        ],
    )

    hits = store.search_hybrid(["vault"], query_text="zzz", query_vector=near, k=5)

    by_path = {h.chunk.source_path: h for h in hits}
    assert by_path["/near.md"].cosine == pytest.approx(1.0, abs=1e-4)
    assert by_path["/far.md"].cosine == pytest.approx(0.0, abs=1e-4)


def test_search_hybrid_exposes_bm25_for_lexical_hits(store: Store) -> None:
    store.ensure_table("vault")
    near = [1.0] + [0.0] * (DIM - 1)
    far = [0.0, 1.0] + [0.0] * (DIM - 2)
    store.upsert_chunks(
        "vault",
        [
            _chunk(path="/lex.md", idx=0, text="quantum entanglement basics", vector=far),
            _chunk(path="/sem.md", idx=0, text="totally different topic", vector=near),
        ],
    )

    hits = store.search_hybrid(["vault"], query_text="quantum entanglement", query_vector=near, k=5)

    by_path = {h.chunk.source_path: h for h in hits}
    assert by_path["/lex.md"].bm25 is not None
    assert by_path["/lex.md"].bm25 > 0.0
    # No lexical overlap for the semantic-only hit — no BM25 signal.
    assert by_path["/sem.md"].bm25 is None


def test_search_vector_sets_cosine_equal_to_score(store: Store) -> None:
    store.ensure_table("vault")
    v = [1.0] + [0.0] * (DIM - 1)
    store.upsert_chunks("vault", [_chunk(path="/a.md", idx=0, text="x", vector=v)])

    hits = store.search_vector(["vault"], query_vector=v, k=1)

    assert hits[0].cosine == pytest.approx(hits[0].score)
