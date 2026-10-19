# Slice 11 — Index freshness in `index_status`

Written after the fact: this slice shipped (commit `1603b77`) before its
spec was checked in. It records the delivered shape so the slice sequence
in `docs/specs/` stays complete.

## Goal

Let a caller tell a stale or failing index apart from a note that doesn't
exist. `index_status` returned chunk counts only, and the scheduled indexer
runs in a separate process, so a run that died at startup (Ollama
unreachable) or failed on individual files left no trace an MCP client
could see.

## Success criteria

### Freshness record (`local_rag.run_status`, new)

- `RunStatusLog(db_path)` owns `.index_status.json` beside the DB.
- Kept separate from the store's `.run_log.json`. That log records only
  runs that changed a table, because it bounds version pruning; recording
  no-op runs there would shrink the undo window.
- `record_run(result, embedding_model=…)` is called after every
  `index_source`, changed or not, and stores per source:

  ```python
  class SourceRunStatus(TypedDict):
      last_attempt: str              # ISO-8601 UTC
      last_run: str | None           # last completed run
      last_change: str | None        # last run that embedded/deleted/emptied a file
      last_error: str | None         # startup failure; cleared by the next good run
      embedding_model: str | None
      failures: list[FailureGroup]   # files the last run couldn't index
  ```

- `failures` groups failed files (`error`, `skipped_unreadable`) by error
  message — `{error, count, example_path}` — so a run where every file hits
  the same error stays one entry whatever the corpus size. Oversize files
  are a policy skip, not a failure.
- `record_error(sources, message)` records a run that died before indexing
  anything (embedder health check failed); `local-rag index` calls it
  before exiting 3.
- Writes are atomic (temp file, then rename). A missing, corrupt, or
  partial record reads as "never indexed", with a warning, never an
  exception.

### `index_status` (MCP)

- Per source: `name`, `chunk_count`, `last_run`, `last_run_age_seconds`,
  `last_change`, `last_attempt`, `last_error`, `embedding_model`,
  `failures`.
- Lists the union of indexed tables and configured sources, so a
  configured source that was never indexed shows up with `chunk_count = 0`
  and null timestamps.
- The MCP server reads the record at call time (it is a long-lived process
  separate from the indexer). The clock is injectable for tests.
- The tool docstring tells the caller to check freshness when an expected
  note is missing from `search`.

## Non-goals

- Alerts or notifications.
- A staleness threshold in config — the caller judges.
- Per-file error lists over MCP (grouped counts plus one example path only).
- Freshness in `local-rag list`.

## Files

- `src/local_rag/run_status.py` (new)
- `src/local_rag/cli.py` — call `record_run` / `record_error`.
- `src/local_rag/mcp_server.py` — `SourceStatus`, `index_status`.
- `docs/claude-integration.md`, `TODO.md`
- `tests/test_cli.py`, `tests/test_mcp_server.py`

## Tests

- A run where nothing changed still records `last_run`.
- `last_change` moves only when a run changed the table.
- Embedder-unreachable startup records `last_error`; the next good run
  clears it.
- Failed files group by error message with a count and example path.
- A missing or corrupt record gives "never indexed" without raising.
- A configured but never-indexed source is listed.
- `last_run_age_seconds` is computed from the injected clock.

## Verification

```
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src/
```
