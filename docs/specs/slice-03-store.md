# Slice 03 — LanceDB store

Third implementation slice. The persistence layer: one LanceDB table per source, with the row schema from the project spec, hybrid search via RRF, and the v1 rule of *no `create_index`* (brute-force vector scan).

## Goal

Implement `local_rag.store.Store` (LanceDB-backed chunk store) and `local_rag.models.Chunk` / `SearchHit` (the canonical row + result shapes shared with the indexer and search code).

## Success criteria

### Models (`local_rag.models`)

```python
@dataclass(frozen=True)
class Chunk:
    source_path: str   # absolute path of the source file
    file_hash: str     # SHA-256 of the file content (used for incremental reindex)
    chunk_index: int   # 0-based within the file
    char_start: int    # offset into source file
    char_end: int      # offset (exclusive)
    heading_path: str  # "/heading 1/heading 2" for md; "" for code
    text: str          # the chunk content (what we embed + search over)
    vector: list[float]
    @property
    def id(self) -> str: ...   # stable per (source_path, chunk_index)


@dataclass(frozen=True)
class SearchHit:
    source_name: str   # which table the hit came from
    chunk: Chunk
    score: float       # higher is better
```

`Chunk.id` is `f"{source_path}::{chunk_index}"`. Stable across reindex; used as the merge-insert key.

### Store (`local_rag.store.Store`)

- `Store(db_path: Path, vector_dim: int)` — opens (or creates) a LanceDB database at `db_path`.
- `ensure_table(source_name: str) -> None` — idempotent. Creates the table with the row schema if missing. Also calls `create_fts_index("text", use_tantivy=False, replace=True)` on first creation so hybrid search works (LanceDB's native FTS).
- `upsert_chunks(source_name: str, chunks: list[Chunk]) -> None` — uses LanceDB `merge_insert` on the `id` column (`when_matched_update_all`, `when_not_matched_insert_all`). Empty list is a no-op.
- `delete_file(source_name: str, source_path: str) -> int` — deletes every row where `source_path` matches. Returns the count removed. Escapes single quotes in the path to avoid SQL-injection-style breaks on weird filenames.
- `search_vector(source_names: list[str], query_vector: list[float], k: int) -> list[SearchHit]` — per-table cosine search via `tbl.search(...).distance_type("cosine").limit(k)`, then union + sort by score, return top-k. Empty `source_names` returns `[]`.
- `search_hybrid(source_names: list[str], query_text: str, query_vector: list[float], k: int) -> list[SearchHit]` — runs vector and FTS sequentially per table (sync; the MCP server can `asyncio.to_thread` later); fuses with Reciprocal Rank Fusion (`1 / (60 + rank)`, summed across both rankers); returns top-k by fused score. Each ranker pulls `max(4*k, 50)` candidates to give RRF enough material.
- `chunk_counts() -> dict[str, int]` — `{source_name: row_count}` over every table in the DB.
- `list_sources() -> list[str]` — table names sorted alphabetically.

### Behavioral rules

- **No `create_index` for vectors** (brute-force scan, per the project spec). FTS index *is* created — it's required for hybrid search, not a vector index.
- **Scores are "higher is better"** uniformly: vector hits convert `_distance` (cosine distance) → `1 - distance` (similarity). FTS gets BM25 score directly. Hybrid emits fused RRF score (unitless, but ordering-stable).
- **No global mutable state.** Each `Store` owns its DB connection.
- **Path quoting**: `source_path` in delete predicates is escaped (`'` → `''`).
- **PyArrow schema** with `pa.list_(pa.float32(), vector_dim)` for the fixed-size vector column.

## Non-goals

- No vector index (`create_index`) — explicitly forbidden by the project spec for v1.
- No batch search (one query at a time is fine for this codebase).
- No async API. The MCP server can offload via `asyncio.to_thread` later.
- No backup / migration / schema-evolution tooling.
- No cross-encoder reranker (deferred per project spec).
- No chunkers, no indexer — that's slices 4 and 5.

## Files

- `src/local_rag/models.py` (new) — `Chunk`, `SearchHit`
- `src/local_rag/store.py` (new) — `Store`
- `tests/test_store.py` (new)

## Tests

- Models:
  - `Chunk.id` derives correctly from source_path + chunk_index.
- CRUD:
  - `ensure_table` creates the table; second call is a no-op.
  - `upsert_chunks` persists rows; querying recovers them.
  - `upsert_chunks` replaces a row with the same id (different text/vector).
  - `upsert_chunks([])` is a no-op (doesn't blow up on empty input).
  - `delete_file` removes all rows for a given source_path; other files untouched.
  - `delete_file` handles paths containing single quotes.
  - `chunk_counts` reflects inserts and deletes.
  - `list_sources` returns table names sorted.
- Search:
  - `search_vector` returns nearest by cosine first.
  - `search_vector` across multiple sources merges + returns top-k overall.
  - `search_vector([], ...)` returns `[]`.
  - `search_hybrid` returns lexical-only matches (no semantic overlap) — proves FTS is contributing.
  - `search_hybrid` returns semantic-only matches (no lexical overlap) — proves vector is contributing.
  - `search_hybrid` ranks a doc matching both rankers above a doc matching one.
  - `search_hybrid([], ...)` returns `[]`.

Tests use a per-test `tmp_path` DB. All vectors in tests are tiny (dim=8) and hand-constructed so RRF behavior is deterministic.

## Verification

```
uv run pytest tests/         # 44 prior + ~17 new
uv run ruff check src/ tests/
uv run mypy src/             # strict
```
