# local-rag

Local semantic + keyword search across your Obsidian vault and source repos,
exposed to Claude Code and Cowork over the Model Context Protocol (MCP).

No cloud APIs. No API keys. No telemetry. Everything — embeddings, vectors,
queries — stays on your machine.

> ## Status
>
> Published as a personal tool, not a managed product. The full design and
> per-slice notes live under [`docs/specs/`](docs/specs/).
>
> **This repo is in-flight.** It is being built out slice-by-slice
> tracked in [`TODO.md`](TODO.md). The config schema, CLI flags, and MCP
> surface are still settling — pin a specific commit if you depend on a
> snapshot.

## How it fits together

```text
  sources                   indexer                store              server               clients
 ─────────────         ──────────────────       ──────────       ────────────────       ───────────────
  Obsidian vault    ─┐                                                                  ┌─ Cowork
  ~/Downloads/src/A ─┼─►  chunker → embedder ─► LanceDB  ─►  MCP stdio server  ───────┤
  ~/Downloads/src/B ─┘    (md headers,         one table        tools:                  └─ Claude Code
                          code line-windows)   per source       - search                    (VS Code)
                                                                - list_sources
                                                                - index_status
```

One [LanceDB] table per configured source, embedded locally with
[Ollama] `bge-m3`. Hybrid search gathers candidates by vector similarity and
BM25 keyword match, then ranks them by cosine similarity plus a small,
bounded keyword boost.

What a model gets from each `search` hit:

- the matching chunk, with its file, heading path and character offsets;
- honest scores: `cosine` for strength, `bm25` for keyword match;
- the note's `note_date`, `status` and `tags`, read from its frontmatter, so
  it can prefer the newer of two disagreeing notes.

`index_status` reports when each source was last indexed and what failed,
so a model can tell a stale index from a missing note.

How each part works and why it was chosen →
[`docs/architecture.md`](docs/architecture.md).

## Requirements

- macOS or Linux
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

Nothing is indexed until you write a config — copy
[`config.example.toml`](config.example.toml) to
`~/.config/local-rag/config.toml` and edit the paths. Every optional key in it
is spelled out at its shipped default.

Then build the index (the first run embeds everything and takes a while):

```bash
uv run local-rag index
uv run local-rag search "something you wrote about"
```

## Upgrading

`git pull && uv sync`, then run `uv run local-rag index`. If it exits with
code 4, the upgrade changed the index schema: run
`uv run local-rag index --force` once to rebuild it. Restart your MCP
clients afterwards so their servers load the new code. Details:
[`docs/deployment.md`](docs/deployment.md#upgrading).

## Next steps

- How it works and why → [`docs/architecture.md`](docs/architecture.md)
- Configure what gets indexed → [`docs/configuration.md`](docs/configuration.md)
- Use it from the CLI (including `eval` for retrieval quality) → [`docs/cli.md`](docs/cli.md)
- Wire it into a Claude client → [`docs/claude-integration.md`](docs/claude-integration.md)
- Run on a schedule (cron, `launchd`) → [`docs/deployment.md`](docs/deployment.md)
- Full spec → [`docs/specs/local-rag.md`](docs/specs/local-rag.md)
- Open work / known gaps → [`TODO.md`](TODO.md)

## Acknowledgements

This project was developed with the assistance of AI tools.

## License

[MIT](LICENSE).

[LanceDB]: https://lancedb.github.io/lancedb/
[Ollama]: https://ollama.com/
[uv]: https://docs.astral.sh/uv/
