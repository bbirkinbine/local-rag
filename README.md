# local-rag

[![AI-Assisted](https://img.shields.io/badge/AI--Assisted-Claude-7c3aed)](#acknowledgements)
[![Python](https://img.shields.io/badge/python-3.12-blue)](https://www.python.org/)

Local semantic + keyword search across your Obsidian vault and source repos,
exposed to Claude Code and Cowork over the Model Context Protocol (MCP).

No cloud APIs. No API keys. No telemetry. Everything — embeddings, vectors,
queries — stays on your machine.

## Why

You already have a lot of context on disk: a years-old vault of notes, dozens
of cloned repos, the occasional research dump. Modern editor-side assistants
can read one file at a time but can't search across all of it. Cloud RAG
services can, but they want your data.

`local-rag` indexes whatever you tell it to into a local [LanceDB] store,
embeds with [Ollama] (`bge-m3`, 1024-dim, L2-normalized), and exposes three
tools over MCP that any MCP-capable client can call:

- `search(query, sources=None, k=10)` — hybrid (vector + BM25) search
- `list_sources()` — what's indexed
- `index_status()` — chunk counts per source

The CLI also runs the same operations directly.

## How it fits together

```
  sources                   indexer                store              server               clients
 ─────────────         ──────────────────       ──────────       ────────────────       ───────────────
  Obsidian vault    ─┐                                                                  ┌─ Cowork
  ~/Downloads/src/A ─┼─►  chunker → embedder ─► LanceDB  ─►  MCP stdio server  ───────┤
  ~/Downloads/src/B ─┘    (md headers,         one table        tools:                  └─ Claude Code
                          code line-windows)   per source       - search                    (VS Code)
                                                                - list_sources
                                                                - index_status
```

One LanceDB table per configured source. Markdown chunks are header-aware
(splits on `^#+ `, attaches a `/H1/H2/…` heading path). Code chunks are
fixed line windows (~60 lines with 10-line overlap). Hybrid search fuses
vector + BM25 results via Reciprocal Rank Fusion.

## Requirements

- macOS or Linux (untested on Windows; the indexer shells out to `git ls-files` when `respect_gitignore = true`)
- Python 3.12 (managed by [uv])
- [Ollama] running locally
- ~600 MB disk for the `bge-m3` model weights

## Install

```bash
git clone https://github.com/<your-user>/local-rag.git
cd local-rag
uv sync
ollama pull bge-m3
```

## Configure

Create `~/.config/local-rag/config.toml`:

```toml
db_path = "~/.local/share/local-rag/db"

[embedding]
provider = "ollama"
model = "bge-m3"
url = "http://localhost:11434"
dim = 1024

[[sources]]
name = "vault"
path = "~/Downloads/obsidian-vault"
type = "markdown"
ignore = [".obsidian/**", ".trash/**"]

[[sources]]
name = "local-rag"
path = "~/Downloads/src/local-rag"
type = "code"
respect_gitignore = true
```

Every source is opt-in. Third-party clones don't get indexed unless you add
a `[[sources]]` block for them. Override the config path with
`--config <path>` or the `LOCAL_RAG_CONFIG` env var.

## Use it from the CLI

```bash
uv run local-rag index              # incremental reindex (SHA-256 file hashes)
uv run local-rag index vault        # just one source
uv run local-rag search "RRF tuning"
uv run local-rag list               # source name + chunk count
uv run local-rag mcp                # MCP server on stdio (for Claude clients)
```

## Wire it into Claude

### Claude Code (VS Code)

```bash
claude mcp add local-rag -- uv --directory /path/to/local-rag run local-rag mcp
```

### Claude Cowork

In the Cowork MCP settings panel, add a new server:

- Command: `uv`
- Args: `--directory /path/to/local-rag run local-rag mcp`

Once registered, the three tools (`search`, `list_sources`, `index_status`)
appear in Claude's tool list automatically; the model decides when to call
them based on the conversation.

## Configuration reference

| Key | Type | Notes |
|---|---|---|
| `db_path` | string | Where LanceDB stores its tables. Created if missing. |
| `embedding.provider` | string | Only `"ollama"` is supported in v1. |
| `embedding.model` | string | Must be pulled into Ollama (`ollama pull <model>`). |
| `embedding.url` | string | Usually `http://localhost:11434`. |
| `embedding.dim` | int | Vector dimension. Must match the model — `bge-m3` is 1024. |
| `[[sources]].name` | string | Unique identifier; one LanceDB table per name. |
| `[[sources]].path` | string | Source directory. `~` expands. Must exist. |
| `[[sources]].type` | enum | `"markdown"` or `"code"`. Advisory only — the chunker dispatches by file extension. |
| `[[sources]].ignore` | list[string] | gitignore-style globs (e.g. `.obsidian/**`). |
| `[[sources]].respect_gitignore` | bool | If true, defer the walk to `git ls-files` in the source root. |

## Indexing rules (v1)

- **Extension allowlist**: `.md`, `.mdx`, `.txt`, `.rst`, `.py`, `.js`, `.jsx`,
  `.ts`, `.tsx`, `.go`, `.rs`, `.toml`, `.yaml`, `.yml`, `.json`. Everything
  else (binaries, images, lockfiles) is silently skipped.
- **Size cap**: files ≥ 1 MB are skipped.
- **Incremental**: SHA-256 of file contents is compared against the stored
  hash. Unchanged files never re-hit the embedder.
- **Orphan deletion**: files removed from disk get their chunks deleted on
  the next index run.
- **No vector index** (`create_index`) in v1 — brute-force scan is fine for
  <100k chunks. Revisit when query latency crosses 500 ms.

## Development

```bash
uv run pytest             # ~155 tests
uv run ruff check .       # lint
uv run ruff format .      # format
uv run mypy src/          # strict type-check
```

Source layout:

```
src/local_rag/
  cli.py          # argparse + subcommand dispatch
  config.py       # TOML loader + validation
  paths.py        # canonical config-path resolution
  embedder.py     # Ollama batch-embed client
  chunkers.py     # markdown header-aware + code line-window
  models.py       # Chunk, SearchHit (frozen dataclasses)
  store.py        # LanceDB-backed store + hybrid search (RRF)
  indexer.py      # walks a Source, syncs disk → store
  mcp_server.py   # FastMCP wiring for the three tools
```

The full spec lives at [`docs/specs/local-rag.md`](docs/specs/local-rag.md).
Per-slice implementation specs at [`docs/specs/slice-*.md`](docs/specs/).

## Acknowledgements

This project was developed with the assistance of AI tools.

## License

TBD — choose and add a `LICENSE` file before making this public.

[LanceDB]: https://lancedb.github.io/lancedb/
[Ollama]: https://ollama.com/
[uv]: https://docs.astral.sh/uv/
