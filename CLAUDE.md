# Project: local-rag

A small Python tool that indexes the Obsidian vault and selected source repos into a local LanceDB store, embeds with Ollama `bge-m3`, and exposes semantic search over MCP (stdio) so both Cowork and Claude Code in VS Code can query notes + code together. Privacy-first: no cloud APIs, no API keys, no quotas. The full spec lives at `docs/specs/local-rag.md`.

## Stack

- Python 3.12 (managed by `uv`)
- LanceDB (one table per source; PyArrow-native; brute-force scan in v1)
- Ollama via HTTP (`httpx`) — `bge-m3` for embeddings (1024-dim, cosine; batch via `/api/embed`)
- MCP Python SDK (stdio server)
- `structlog` for logging; `tomllib` (stdlib) for config
- pytest + pytest-asyncio
- ruff (lint + format) + mypy (strict)

## How to run things

- Install: `uv sync`
- CLI: `uv run local-rag index | search "q" | list | mcp` (entry point `local_rag.cli:main`)
- Run tests: `uv run pytest`
- Lint: `uv run ruff check . && uv run ruff format --check .`
- Type-check: `uv run mypy src/`
- Single test: `uv run pytest path/to/test.py::test_name -xvs`

## Conventions

- **Files ≤ 300 lines.** Split aggressively; one concept per file.
- **Type hints required** on every function signature. `Any` requires a comment justifying it.
- **No bare `except:`**. Catch specific exceptions or `Exception` with a re-raise/log.
- **Docstrings:** Google-style. One-liner for trivial helpers; full args/returns/raises for public functions.
- **Imports:** absolute imports inside the package; relative only inside `__init__.py`.
- **Logging:** `structlog`, not `print` or `logging` directly. Get a logger via `log = structlog.get_logger()`.

## Testing rules

- **Tests come first.** Before any implementation change, write or update pytest tests that fail. Show me the failing test output, then proceed to implementation.
- Tests live under `tests/` mirroring the `src/` tree.
- Use `pytest` fixtures, not setup/teardown methods.
- No mocks of the LanceDB store — use a temp-dir DB fixture.
- Ollama-dependent tests must skip cleanly when Ollama is unreachable (don't fail CI). Use `pytest.mark.skipif` or a fixture-level skip on a health-check probe.
- One assertion per test where reasonable; multi-assertion only when verifying a single behavior.

## Don't-touch list / hard rules (from spec)

- **Don't** call LanceDB `create_index` in v1. Brute-force scan is fine for <100k chunks. The trigger to revisit is query latency >500 ms.
- **Don't** add `langchain` or `llama-index` as deps. Do chunking inline — this is a ~200-line job.
- **Don't** embed binary files. Use a strict extension allowlist (`.md`, `.txt`, `.rst`, `.py`, `.js`, `.ts`, `.go`, `.rs`, `.toml`, `.yaml`, `.json`, etc.); skip files >1 MB.
- **Don't** assume Ollama is running. Health-check on every CLI startup; surface a clear error if unreachable.
- **Don't** index by default. Every source must be opt-in via a `[[sources]]` block in the config; third-party clones under `~/Downloads/src/` stay invisible unless explicitly added.
- `pyproject.toml` `[tool.uv]` section — ask first.

## Open work / current state (updated 2026-05-11)

- Bootstrap complete from the agentic starter-files; no implementation code under `src/local_rag/` yet.
- Full spec at `docs/specs/local-rag.md` — read this before any slice.
- Next: implement in vertical slices via the agentic loop (planner → test-first → implement → reviewer). Proposed order: config/paths → embedder client → store → chunkers → indexer → CLI → MCP server.
- `uv sync` not run yet — happens at the start of the first slice so the lockfile reflects an actual import graph.

## Repository / publishing

- **This is a public GitHub repo** (`github.com/bbirkinbine/local-rag`). Commit messages, PR descriptions, branch names, and code comments are all public — write them accordingly.
- **Licensed MIT** (see `LICENSE`). Don't add per-file license headers; the top-level `LICENSE` covers everything.
- **No `Co-Authored-By: Claude` (or any AI co-author) trailers** on commits or PRs. The top-level `README.md` already acknowledges AI tooling — that's the single source of attribution.
- **No "Generated with Claude Code" footers** in PR bodies either.

## Style preferences

- Be concise in PR descriptions and commit messages. No emoji unless I ask.
- When you're done, output a one-paragraph summary and the test results — no longer.
- If you'd touch > 5 files, stop and ask first.
