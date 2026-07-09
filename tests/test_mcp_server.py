"""Tests for local_rag.mcp_server — tool handlers exercised directly.

The MCP transport itself is FastMCP's responsibility; here we test the
underlying functions that FastMCP wraps. That lets us reuse the same
FakeEmbedder + tmp_path Store pattern without spinning up the JSON-RPC
machinery.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from local_rag.config import Source
from local_rag.indexer import Indexer
from local_rag.mcp_server import build_tools
from local_rag.store import Store

DIM = 4


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.health_check_called = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[1.0 + 0.01 * i] + [0.0] * (DIM - 1) for i, _ in enumerate(texts)]

    def health_check(self) -> None:
        self.health_check_called = True


def _make_source(name: str, path: Path) -> Source:
    return Source(
        name=name,
        path=path,
        type="markdown",  # type: ignore[arg-type]
        ignore=[],
        respect_gitignore=False,
    )


def _seed(store: Store, src_dir: Path, name: str, files: dict[str, str]) -> None:
    sub = src_dir / name
    sub.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        (sub / rel).write_text(content)
    Indexer(store, FakeEmbedder()).index_source(_make_source(name, sub))


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "db", vector_dim=DIM)


# ----------------------------------------------------------- list_sources ---


def test_list_sources_empty_store_returns_empty(store: Store) -> None:
    tools = build_tools(store, FakeEmbedder(), configured_sources=[])

    assert tools.list_sources() == []


def test_list_sources_returns_chunk_counts(store: Store, tmp_path: Path) -> None:
    _seed(store, tmp_path, "vault", {"a.md": "# A\n\nbody\n"})
    _seed(store, tmp_path, "code", {"b.md": "# B\n\nthing\n"})
    tools = build_tools(store, FakeEmbedder(), configured_sources=["vault", "code"])

    result = tools.list_sources()

    names = {entry["name"] for entry in result}
    assert names == {"vault", "code"}
    assert all(entry["chunk_count"] > 0 for entry in result)


# ----------------------------------------------------------- index_status ---


def test_index_status_empty_store(store: Store) -> None:
    tools = build_tools(store, FakeEmbedder(), configured_sources=[])

    status = tools.index_status()

    assert status == {"total_chunks": 0, "sources": []}


def test_index_status_reflects_indexed_state(store: Store, tmp_path: Path) -> None:
    _seed(store, tmp_path, "vault", {"a.md": "# A\n\nbody\n"})
    _seed(store, tmp_path, "vault2", {"b.md": "# B\n\nx\n"})
    tools = build_tools(store, FakeEmbedder(), configured_sources=["vault", "vault2"])

    status = tools.index_status()

    by_name = {s["name"]: s["chunk_count"] for s in status["sources"]}
    assert set(by_name) == {"vault", "vault2"}
    assert status["total_chunks"] == sum(by_name.values())
    assert status["total_chunks"] > 0


# ------------------------------------------------------------------ search ---


def test_search_returns_hits_with_expected_shape(store: Store, tmp_path: Path) -> None:
    _seed(store, tmp_path, "vault", {"a.md": "# Heading\n\nbody text\n"})
    embedder = FakeEmbedder()
    tools = build_tools(store, embedder, configured_sources=["vault"])

    hits = tools.search("body", sources=None, k=5)

    assert hits
    h = hits[0]
    assert set(h.keys()) == {
        "score",
        "cosine",
        "bm25",
        "source_name",
        "source_path",
        "heading_path",
        "char_start",
        "char_end",
        "text",
    }
    assert h["source_name"] == "vault"
    assert "body text" in h["text"]


def test_search_exposes_cosine_similarity(store: Store, tmp_path: Path) -> None:
    _seed(store, tmp_path, "vault", {"a.md": "# Heading\n\nbody text\n"})
    tools = build_tools(store, FakeEmbedder(), configured_sources=["vault"])

    hits = tools.search("body", sources=None, k=5)

    assert hits
    assert isinstance(hits[0]["cosine"], float)
    assert -1.0 <= hits[0]["cosine"] <= 1.0


def test_search_empty_query_returns_empty(store: Store, tmp_path: Path) -> None:
    _seed(store, tmp_path, "vault", {"a.md": "# A\n\nbody\n"})
    tools = build_tools(store, FakeEmbedder(), configured_sources=["vault"])

    assert tools.search("", sources=None, k=5) == []
    assert tools.search("   \n", sources=None, k=5) == []


def test_search_unknown_source_in_filter_is_ignored(store: Store, tmp_path: Path) -> None:
    _seed(store, tmp_path, "vault", {"a.md": "# A\n\nbody\n"})
    tools = build_tools(store, FakeEmbedder(), configured_sources=["vault"])

    # "no_such_source" is ignored; "vault" still searched.
    hits = tools.search("body", sources=["no_such_source", "vault"], k=5)

    assert hits
    assert all(h["source_name"] == "vault" for h in hits)


def test_search_all_unknown_sources_returns_empty(store: Store, tmp_path: Path) -> None:
    _seed(store, tmp_path, "vault", {"a.md": "# A\n\nbody\n"})
    tools = build_tools(store, FakeEmbedder(), configured_sources=["vault"])

    assert tools.search("body", sources=["nope"], k=5) == []


def test_search_empty_sources_list_returns_empty(store: Store, tmp_path: Path) -> None:
    """sources=[] means 'the caller explicitly asked for nothing', distinct
    from sources=None which means 'use defaults'."""
    _seed(store, tmp_path, "vault", {"a.md": "# A\n\nbody\n"})
    tools = build_tools(store, FakeEmbedder(), configured_sources=["vault"])

    assert tools.search("body", sources=[], k=5) == []
    # None still defaults to all configured.
    assert tools.search("body", sources=None, k=5)


def test_search_clamps_k_via_store_call(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spy on store.search_hybrid to assert the clamped k actually reaches it."""
    _seed(store, tmp_path, "vault", {"a.md": "# A\n\nbody\n"})
    tools = build_tools(store, FakeEmbedder(), configured_sources=["vault"])

    seen_k: list[int] = []
    original = store.search_hybrid

    def spy(*args: object, **kwargs: object) -> object:
        seen_k.append(int(kwargs["k"]))
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "search_hybrid", spy)

    tools.search("body", sources=None, k=0)
    tools.search("body", sources=None, k=-3)
    tools.search("body", sources=None, k=500)
    tools.search("body", sources=None, k=7)

    assert seen_k == [1, 1, 100, 7]


def test_search_returns_empty_when_no_sources_indexed(store: Store) -> None:
    """No tables exist → search should not crash; returns []."""
    tools = build_tools(store, FakeEmbedder(), configured_sources=["vault"])

    assert tools.search("anything", sources=None, k=5) == []
