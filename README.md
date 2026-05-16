# local-rag

Local semantic + keyword search across your Obsidian vault and source repos,
exposed to Claude Code and Cowork over the Model Context Protocol (MCP).

No cloud APIs. No API keys. No telemetry. Everything — embeddings, vectors,
queries — stays on your machine.

> ## Status
>
> Published as a personal tool, not a managed product. Issues and PRs are
> welcome but won't get fast turnaround. The [`docs/`](docs/) tree —
> especially the full spec at
> [`docs/specs/local-rag.md`](docs/specs/local-rag.md) and the client wiring
> guide at [`docs/claude-integration.md`](docs/claude-integration.md) — is
> the part most likely to be useful to others.
>
> **This repo is in-flight.** The config schema, CLI flags, and MCP surface
> are still settling. Open work is tracked in [`TODO.md`](TODO.md); per-slice
> specs under [`docs/specs/`](docs/specs/) record what's been built. Pin a
> specific commit if you depend on a snapshot.

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

One LanceDB table per configured source. Markdown chunks are header-aware;
code chunks are fixed line windows. Hybrid search fuses vector + BM25
results via Reciprocal Rank Fusion. Details and tunables live in the
[spec](docs/specs/local-rag.md).

## Requirements

- macOS or Linux (untested on Windows; the indexer shells out to `git ls-files` when `respect_gitignore = true`)
- Python 3.12 (managed by [uv])
- [Ollama] running locally
- ~600 MB disk for the `bge-m3` model weights

## Install

```bash
git clone https://github.com/bbirkinbine/local-rag.git
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
`--config <path>` or the `LOCAL_RAG_CONFIG` env var. The full key reference
and indexing rules (extension allowlist, size cap, incremental hashing) are
in the [spec](docs/specs/local-rag.md).

## Use it from the CLI

```bash
uv run local-rag index              # incremental reindex (SHA-256 file hashes)
uv run local-rag index vault        # just one source
uv run local-rag search "RRF tuning"
uv run local-rag list               # source name + chunk count
uv run local-rag mcp                # MCP server on stdio
```

## Wire it into Claude

### Claude Code (CLI + VS Code) — stdio

```bash
claude mcp add local-rag -- uv --directory /path/to/local-rag run local-rag mcp
```

Restart Claude Code; the three tools (`search`, `list_sources`,
`index_status`) appear automatically.

### Claude Cowork (desktop)

Cowork wiring is in flight — see [`TODO.md`](TODO.md) for status. The
in-progress plugin scaffolding lives under
[`claude-plugin/`](claude-plugin/) and the spec is at
[`docs/specs/slice-10-cowork-plugin.md`](docs/specs/slice-10-cowork-plugin.md).

### Claude.ai (web)

Not recommended — web Claude only accepts public-internet MCP URLs, which
would mean tunneling your vault through a third-party provider, directly at
odds with the project's privacy-first design. See
[docs/claude-integration.md](docs/claude-integration.md#claudeai-web) for
the rationale.

## Running it on a schedule

For "always-on" indexing (cron or `launchd` firing every 30 min) and other
deployment notes, see [docs/deployment.md](docs/deployment.md). The indexer
and any running MCP server are safe to run concurrently — LanceDB uses
snapshot semantics.

## Development

```bash
uv run pytest             # tests
uv run ruff check .       # lint
uv run ruff format .      # format
uv run mypy src/          # strict type-check
```

Source under [`src/local_rag/`](src/local_rag/); each module has a
single-line module docstring describing its role. Full spec at
[`docs/specs/local-rag.md`](docs/specs/local-rag.md).

## Acknowledgements

This project was developed with the assistance of AI tools.

## License

[MIT](LICENSE).

[LanceDB]: https://lancedb.github.io/lancedb/
[Ollama]: https://ollama.com/
[uv]: https://docs.astral.sh/uv/
