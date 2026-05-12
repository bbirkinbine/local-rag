# Slice 05 — indexer

Fifth implementation slice. Tie the chunker, embedder, and store together.
This is the slice that makes `local-rag index` work.

## Goal

Implement `local_rag.indexer.Indexer` — the orchestrator that walks a
`Source`, discovers eligible files, embeds the new/changed ones in batch via
the embedder, and upserts them through the store. Idempotent and incremental
via SHA-256.

## Success criteria

### Discovery rules

For a given `Source`:

- **Extension allowlist** (hardcoded, universal):
  `.md, .mdx, .txt, .rst, .py, .js, .jsx, .ts, .tsx, .go, .rs, .toml, .yaml, .yml, .json`.
  Anything else (binaries, images, lockfiles with unknown extensions) is
  silently skipped — this is the "don't embed binaries" rule from the project
  spec.
- **Size cap**: 1,048,576 bytes. Files at or above this are skipped (status:
  `skipped_oversize`). Size is checked via `stat()` before reading.
- **Per-source `ignore` globs**: matched against the path relative to the
  source root using `pathspec` with gitignore-style semantics (so
  `.obsidian/**` works correctly).
- **`respect_gitignore=True`** (code sources): defers the walk to
  `git ls-files --cached --others --exclude-standard` in the source root.
  This handles nested `.gitignore` files and `.git/info/exclude` natively.
  Falls back to the plain walk (with a warning logged) if the source isn't a
  git repo.
- **Symlinks**: followed (`Path.iterdir()` default). Out of scope: cycle
  detection — sources shouldn't contain symlink loops in practice.

### Incremental rule

The indexer reads the *currently stored* file hashes via a new store method
(`Store.file_hashes(source_name) -> dict[source_path -> hash]`) and:

- If a discovered file's SHA-256 equals the stored hash → skip it.
- If the file is new or changed → chunk it, embed *all its chunks in a single
  batched embedder call*, and upsert.
- If a stored path is no longer on disk (or no longer eligible) → delete it
  from the table.

### Embedder integration

The indexer takes an `Embedder` Protocol-typed dep (`def embed(texts: list[str]) -> list[list[float]]`).
The real `OllamaEmbedder.embed` satisfies it; tests use a fake. Failure on
one file (embedder raises, store raises, file unreadable) is logged and that
file's status is `error` — other files keep going.

### Public API

```python
class Indexer:
    def __init__(
        self,
        store: Store,
        embedder: Embedder,
        *,
        max_file_bytes: int = 1_048_576,
        allowed_extensions: frozenset[str] | None = None,  # for tests
    ) -> None: ...

    def index_source(self, source: Source) -> IndexResult:
        """Idempotent. Ensures the table, syncs disk → store."""


@dataclass(frozen=True)
class FileResult:
    path: Path
    status: Literal[
        "embedded", "unchanged", "deleted",
        "skipped_oversize", "skipped_unreadable", "error",
    ]
    chunk_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class IndexResult:
    source_name: str
    files: list[FileResult]
```

`IndexResult` is the structured payload the CLI/MCP can summarize later.

### Store addition

One new method on `Store`:

```python
def file_hashes(self, source_name: str) -> dict[str, str]:
    """Return {source_path: file_hash} for every row in the table.

    Implementation: scan all rows, project just the two columns, and
    deduplicate (each unique source_path has one hash; we take the first).
    """
```

### Behavioral rules

- The indexer never opens an `httpx.Client` itself — it gets one through the
  `Embedder` dep. (Production wiring lives in the CLI slice.)
- Reads files as UTF-8 with `errors="replace"` — binary-flavored text (UTF-16
  BOMs, latin-1 source files) shouldn't crash a vault index.
- A file that fails to read (permission denied, decode error after replace
  somehow) → `skipped_unreadable`.
- Logging via `structlog`: one info line per non-trivial transition
  (embedded, deleted, error). Skipped/unchanged files don't log per-file
  (would drown the vault).
- `pathspec` is added to `[project] dependencies`.

## Non-goals

- No watch mode — manual reindex only.
- No parallel file processing (sequential is fine for <100k chunks).
- No retry on embedder failure — one shot, error status, move on.
- No partial-file embedding fallback — if a 200-chunk file's batch embed
  fails, the whole file errors.
- No CLI wiring — that's slice 6.
- No MCP wiring — slice 7.
- No `.gitattributes` honoring (LFS pointers, eol filters).
- No `.dockerignore` or other ignore-file flavors.

## Files

- `pyproject.toml` — add `pathspec` to `[project] dependencies`.
- `src/local_rag/indexer.py` (new) — `Indexer`, `IndexResult`, `FileResult`,
  `Embedder` Protocol, plus internal `_iter_source_files` and `_file_sha256`.
- `src/local_rag/store.py` — add `file_hashes` method.
- `tests/test_indexer.py` (new).
- `tests/test_store.py` — add one test for `file_hashes`.

## Tests

Discovery:
- Allowlist enforced (`.png`, `.bin`, `.lock` files skipped).
- Files at or over the size cap → `skipped_oversize`.
- `Source.ignore` globs honored (`.obsidian/**` skips deep paths).
- `respect_gitignore=True` in a git repo: `.gitignore`-matched files don't
  appear; tracked files do.
- `respect_gitignore=True` in a non-git dir: falls back, finds files (no
  crash).
- Hidden directories (`.git/`, `.venv/`) are skipped by default? — for v1 we
  rely on `.gitignore` / explicit `ignore` to skip them; not a separate rule.

Hashing:
- SHA-256 is stable across calls for the same content.
- Different content → different hash.

Indexing (uses a real `Store` + tmp_path DB + fake embedder):
- First run on a fresh source → all files embedded; row count matches chunks.
- Second run, no changes → every file is `unchanged`; embedder called zero
  times.
- File mutated → embedder called only for the changed file; chunks replaced.
- File deleted from disk → `FileResult.status == "deleted"`; chunks gone.
- New file added → embedded.
- One bad file (read-fail simulated) doesn't stop the others.
- Embedder raise on one file → that file's status is `error`; others succeed.
- IndexResult counts match what actually happened.

Store:
- `file_hashes` returns `{}` for an empty table.
- `file_hashes` reflects current state after upserts and deletes.

## Verification

```
uv sync                       # picks up pathspec
uv run pytest tests/          # 98 prior + ~15 new
uv run ruff check src/ tests/
uv run mypy src/              # strict
```
