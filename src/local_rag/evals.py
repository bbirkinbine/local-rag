"""Golden-query retrieval eval harness.

Runs a set of golden queries (query -> expected file paths) against the live
index and reports recall@k and MRR at the *file* level: chunk hits are
collapsed to their source file, keeping the best (lowest) rank per file.

Negative queries assert the opposite: ``expect_max_cosine`` (instead of
``expected_paths``) means "nothing relevant exists — no hit may reach this
cosine". They guard against score inflation and validate that a caller can
treat weak cosines as "no results". Negatives report pass/fail separately
and never count toward recall/MRR.

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
    """One golden query: the search text plus exactly one expectation —
    ``expected_paths`` (files that should rank) or ``expect_max_cosine``
    (a negative query: no hit may reach this cosine)."""

    query: str
    expected_paths: tuple[str, ...] = ()
    expect_max_cosine: float | None = None

    @property
    def is_negative(self) -> bool:
        return self.expect_max_cosine is not None


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
    max_cosine: float | None = None

    @property
    def passed(self) -> bool:
        """Positive queries pass on a top-k hit; negatives pass when no hit
        reaches the cosine threshold."""
        if self.query.is_negative:
            assert self.query.expect_max_cosine is not None
            return (self.max_cosine or 0.0) < self.query.expect_max_cosine
        return self.best_rank is not None


@dataclass(frozen=True)
class EvalReport:
    """Aggregate metrics over a golden-query run."""

    results: tuple[QueryResult, ...]
    k: int

    @property
    def positives(self) -> tuple[QueryResult, ...]:
        return tuple(r for r in self.results if not r.query.is_negative)

    @property
    def recall(self) -> float:
        """Fraction of positive queries whose expected file is in the top-k."""
        positives = self.positives
        if not positives:
            return 0.0
        return sum(1 for r in positives if r.best_rank is not None) / len(positives)

    @property
    def mrr(self) -> float:
        """Mean reciprocal rank of the first expected file (0 for misses)."""
        positives = self.positives
        if not positives:
            return 0.0
        return sum(1.0 / r.best_rank for r in positives if r.best_rank) / len(positives)

    @property
    def negatives_total(self) -> int:
        return sum(1 for r in self.results if r.query.is_negative)

    @property
    def negatives_passed(self) -> int:
        return sum(1 for r in self.results if r.query.is_negative and r.passed)


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
        threshold = entry.get("expect_max_cosine")
        if expected is not None and threshold is not None:
            raise EvalError(
                f"golden file {path}: queries[{i}] must set 'expected_paths' or "
                "'expect_max_cosine', not both"
            )
        if threshold is not None:
            if not isinstance(threshold, int | float) or not 0.0 < float(threshold) <= 1.0:
                raise EvalError(
                    f"golden file {path}: queries[{i}] 'expect_max_cosine' must be in (0, 1]"
                )
            queries.append(GoldenQuery(query=query, expect_max_cosine=float(threshold)))
            continue
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
        max_cosine = max((h.cosine or 0.0) for h in hits) if hits else 0.0

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
                max_cosine=max_cosine,
            )
        )
    return EvalReport(results=tuple(results), k=k)
