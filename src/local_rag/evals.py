"""Golden-query retrieval eval harness.

Runs a set of golden queries (query -> expected file paths) against the live
index and reports recall@k and MRR at the *file* level: chunk hits are
collapsed to their source file, keeping the best (lowest) rank per file.

Golden files are TOML with repeated ``[[queries]]`` blocks (see
``eval/golden.example.toml``). Real golden data names personal vault notes,
so it lives in gitignored ``*.local.toml`` files.

Expected paths are suffix-matched on path-component boundaries, so a golden
entry can name a vault-relative path (``notes/a.md``) without knowing how
the indexer prefixed it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from local_rag.store import Store


class EvalError(Exception):
    """A golden file is missing or malformed, or there is nothing to evaluate."""


class QueryEmbedder(Protocol):
    """The one embedder capability the harness needs."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class GoldenQuery:
    """One golden query: the search text and the file(s) that should rank."""

    query: str
    expected_paths: tuple[str, ...]


@dataclass(frozen=True)
class QueryResult:
    """Outcome of one golden query against the index.

    ``best_rank`` is the 1-based rank of the first expected file within the
    top-k retrieved files, or ``None`` if no expected file made the cut.
    """

    query: GoldenQuery
    retrieved_paths: tuple[str, ...]
    best_rank: int | None
    matched_path: str | None


@dataclass(frozen=True)
class EvalReport:
    """Aggregate metrics over a golden-query run."""

    results: tuple[QueryResult, ...]
    k: int

    @property
    def recall(self) -> float:
        """Fraction of queries whose expected file appears in the top-k."""
        return sum(1 for r in self.results if r.best_rank is not None) / len(self.results)

    @property
    def mrr(self) -> float:
        """Mean reciprocal rank of the first expected file (0 for misses)."""
        return sum(1.0 / r.best_rank for r in self.results if r.best_rank) / len(self.results)


def load_golden_queries(path: Path) -> list[GoldenQuery]:
    """Parse a golden-query TOML file.

    Args:
        path: TOML file with ``[[queries]]`` blocks, each carrying a
            ``query`` string and a non-empty ``expected_paths`` list.

    Returns:
        The golden queries in file order.

    Raises:
        EvalError: if the file is missing, unparsable, or any entry lacks a
            ``query`` or a non-empty ``expected_paths``.
    """
    if not path.is_file():
        raise EvalError(f"golden file not found: {path}")
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise EvalError(f"golden file {path} is not valid TOML: {e}") from e

    raw = data.get("queries", [])
    if not raw:
        raise EvalError(f"golden file {path} contains no queries")

    queries: list[GoldenQuery] = []
    for i, entry in enumerate(raw):
        query = entry.get("query")
        if not isinstance(query, str) or not query.strip():
            raise EvalError(f"golden file {path}: queries[{i}] has no 'query' string")
        expected = entry.get("expected_paths")
        if not isinstance(expected, list) or not expected:
            raise EvalError(f"golden file {path}: queries[{i}] needs a non-empty 'expected_paths'")
        queries.append(GoldenQuery(query=query, expected_paths=tuple(str(p) for p in expected)))
    return queries


def path_matches(hit_path: str, expected_path: str) -> bool:
    """True if ``hit_path`` equals ``expected_path`` or ends with it on a
    path-component boundary (so ``b.md`` never matches ``ab.md``)."""
    return hit_path == expected_path or hit_path.endswith("/" + expected_path)


def evaluate(
    store: Store,
    embedder: QueryEmbedder,
    source_names: list[str],
    queries: list[GoldenQuery],
    k: int = 5,
) -> EvalReport:
    """Run every golden query through hybrid search and score the results.

    Retrieval fetches ``max(4*k, 20)`` chunks so that after collapsing chunks
    to files there are still at least k distinct files to rank.

    Args:
        store: the chunk store to search.
        embedder: embeds each query text for the vector half of the search.
        source_names: store tables to search (must already exist).
        queries: the golden set.
        k: file-level cutoff for recall@k.

    Returns:
        Per-query results plus aggregate recall@k and MRR.

    Raises:
        EvalError: if ``queries`` is empty.
    """
    if not queries:
        raise EvalError("no queries to evaluate")

    chunk_k = max(4 * k, 20)
    results: list[QueryResult] = []
    for golden in queries:
        vector = embedder.embed([golden.query])[0]
        hits = store.search_hybrid(
            source_names,
            query_text=golden.query,
            query_vector=vector,
            k=chunk_k,
        )
        files: list[str] = []
        for hit in hits:
            if hit.chunk.source_path not in files:
                files.append(hit.chunk.source_path)
        top_files = files[:k]

        best_rank: int | None = None
        matched: str | None = None
        for rank, path in enumerate(top_files, start=1):
            if any(path_matches(path, exp) for exp in golden.expected_paths):
                best_rank, matched = rank, path
                break
        results.append(
            QueryResult(
                query=golden,
                retrieved_paths=tuple(top_files),
                best_rank=best_rank,
                matched_path=matched,
            )
        )
    return EvalReport(results=tuple(results), k=k)
