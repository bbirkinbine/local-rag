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

One [LanceDB] table per configured source. Hybrid search fuses vector + BM25
results via Reciprocal Rank Fusion.

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

## Next steps

- Configure what gets indexed → [`docs/configuration.md`](docs/configuration.md)
- Use it from the CLI → [`docs/cli.md`](docs/cli.md)
- Wire it into a Claude client → [`docs/claude-integration.md`](docs/claude-integration.md)
  (Cowork plugin scaffolding: [`claude-plugin/`](claude-plugin/))
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
