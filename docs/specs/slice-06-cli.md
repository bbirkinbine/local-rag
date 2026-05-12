# Slice 06 — CLI

Sixth implementation slice. Wire everything into the `local-rag` command
that's already registered as the project's console script
(`pyproject.toml [project.scripts]`).

## Goal

Implement `local_rag.cli.main` plus three subcommands: `index`, `search`,
`list`. The `mcp` subcommand is deferred to slice 7 — not stubbed.

## Success criteria

### Subcommands

```
local-rag index [SOURCE [SOURCE …]]   # reindex all, or just the named source(s)
local-rag search QUERY [--sources S [S …]] [-k N]
local-rag list                         # one line per source: name, chunk count
```

- `index` defaults to "all sources in the config".
- `index <name>` reindexes only that source. Unknown names → exit 2 with a
  message listing the configured sources.
- `search QUERY` defaults to searching across every configured source. `-k`
  default is 10. Output is a readable list — one block per hit with score,
  source name, path, heading_path (if any), and the chunk text indented.
- `list` prints `<source_name>\t<chunk_count>` for every table currently in
  the DB (sorted alphabetically). Works even if Ollama is unreachable.

### Wiring

`main(argv: list[str] | None = None) -> int` is the entry point declared in
`pyproject.toml`. Behavior:

1. Parse args with `argparse`. `--config PATH` overrides the env / default.
2. Load `Config` via `Config.load(path)`. On `ConfigError` → exit 2 with the
   message on stderr.
3. Build `Store(config.db_path, vector_dim=config.embedding.dim)`.
4. For `index` / `search`: build `OllamaEmbedder` and run
   `health_check()`. On `EmbedderError` → exit 3 with the message
   (the embedder already includes the "ollama pull <model>" hint).
   `list` skips this step — it must work offline.
5. Dispatch and return the appropriate exit code.

### Exit codes

- `0` — success.
- `1` — runtime failure (caught exception during indexing/searching).
- `2` — bad CLI usage / config (argparse errors, unknown source name,
  `ConfigError`).
- `3` — embedder / Ollama unreachable.

### Logging

`structlog` for internal events. CLI **user-facing** output goes to stdout;
all log lines (info/warning/error) go to stderr. The CLI is also the place
where structlog is *configured* — until now no module configures it, so
`structlog.get_logger()` writes to stderr in a JSON-ish dev format, which is
fine. The CLI installs a simple stderr renderer so log output is readable
(one line per event, no JSON).

### Search output format

```
score=0.872  vault  /path/to/file.md  /Heading 1/Heading 2
    indented chunk text here...
    second line...

score=0.811  code  /path/to/file.py
    chunk text...
```

Empty heading_path is omitted (no trailing tab / `""`). Trailing blank line
between hits. If zero hits: print `no results` to stdout.

## Non-goals

- No `mcp` subcommand (slice 7).
- No interactive prompts. CLI is one-shot.
- No `--json` output flag. Defer until something needs it.
- No `-v/-q` verbosity flags. structlog default is fine for v1.
- No progress bar during indexing. Just per-file log lines from the indexer
  + a final summary.
- No `local-rag init` to generate a config skeleton. The README will paste
  one.
- No source filtering by glob in `search`. Exact source names only.

## Files

- `src/local_rag/cli.py` (new)
- `tests/test_cli.py` (new)

## Tests

`main()` is testable: pass `argv` directly. Tests use:
- A real `Store` against a `tmp_path` DB.
- A fake embedder (the same `FakeEmbedder` pattern as test_indexer; lifted to
  a tests-shared helper or duplicated — duplicated is fine for v1).
- A config TOML written into `tmp_path` and pointed at via `--config`.

Cases:
- `list` with no sources indexed yet → exits 0, prints zero or empty.
- `list` after indexing two sources → exits 0, prints both with their counts.
- `index` (no args) on a 2-source config → both indexed; exit 0; stdout
  reports counts.
- `index <name>` on one of the sources → only that source indexed.
- `index unknown` → exits 2, mentions valid sources.
- `search "q"` after indexing returns hits → exit 0, output contains the
  score-prefixed line.
- `search "q"` with no matches → exit 0, prints `no results`.
- `--config` honored.
- Missing config file → exit 2 with a useful message.

For Ollama-dependent paths, tests inject the embedder rather than spinning
up real Ollama. CLI imports `OllamaEmbedder` at call time via a small
factory function that tests monkey-patch, keeping the production wiring
intact while making the tests fast and deterministic.

## Verification

```
uv run pytest tests/          # 120 prior + ~10 new
uv run ruff check src/ tests/
uv run mypy src/              # strict
uv run local-rag --help       # smoke: argparse renders
```
