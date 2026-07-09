# CLI

`local-rag` ships a small CLI that indexes, searches, and runs the MCP server
directly. The same operations are exposed over MCP for client use.

```bash
uv run local-rag index              # incremental reindex (SHA-256 file hashes)
uv run local-rag index vault        # just one source
uv run local-rag index --force      # re-chunk + re-embed everything (after a chunker change)
uv run local-rag search "chunk overlap handling" -k 5
uv run local-rag list               # source name + chunk count
uv run local-rag eval               # golden-query retrieval eval (recall@k / MRR)
uv run local-rag mcp                # MCP server on stdio
```

`search` hits are ordered by `score` (cosine similarity plus a small
keyword boost); each hit also shows the raw `cos=` and, for lexical
matches, `bm25=` signals. `eval` reads a golden-query TOML
(default `eval/golden.local.toml`; see `eval/golden.example.toml` for the
format, including negative queries via `expect_max_cosine`).

All commands honor `--config <path>` or the `LOCAL_RAG_CONFIG` env var; see
[`configuration.md`](configuration.md) for the config schema.

For wiring the `mcp` subcommand into a Claude client, see
[`claude-integration.md`](claude-integration.md).
