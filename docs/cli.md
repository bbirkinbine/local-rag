# CLI

`local-rag` ships a small CLI that indexes, searches, and runs the MCP server
directly. The same operations are exposed over MCP for client use.

```bash
uv run local-rag index              # incremental reindex (SHA-256 file hashes)
uv run local-rag index vault        # just one source
uv run local-rag index --force      # re-chunk + re-embed everything; rebuilds outdated tables
uv run local-rag search "chunk overlap handling" -k 5
uv run local-rag list               # source name + chunk count
uv run local-rag eval               # golden-query retrieval eval (recall@k / MRR)
uv run local-rag mcp                # MCP server on stdio
```

`search` hits are ordered by `score` (cosine similarity plus a small
keyword boost); each hit also shows the raw `cos=` and, for lexical
matches, `bm25=` signals, then `date=` and `status=` when the note's
frontmatter has them (see
[configuration](configuration.md#frontmatter-metadata)). `eval` reads a golden-query TOML
(default `eval/golden.local.toml`; see `eval/golden.example.toml` for the
format, including negative queries via `expect_max_cosine`).

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success. |
| `2` | Usage or config error (including an unknown source name). |
| `3` | Ollama unreachable (embedder health check failed). |
| `4` | `index` refused: a source's table was built with an older schema. Run `local-rag index --force`. |

## Schema upgrades

When an upgrade adds columns to the index, `local-rag index` refuses to run
(exit 4) until `local-rag index --force` rebuilds the outdated tables. It
checks every targeted source first and touches nothing if any is outdated;
the refusal is also recorded as the source's `last_error`, so the MCP
`index_status` tool shows it. `--force` drops each outdated table and
re-embeds it in full, so searches against that source return partial results
until the rebuild finishes. Searching an outdated table keeps working in the
meantime; the new fields just come back empty. A scheduled indexer keeps
failing with exit 4 until the one-off `--force` run.

All commands honor `--config <path>` or the `LOCAL_RAG_CONFIG` env var; see
[`configuration.md`](configuration.md) for the config schema.

For wiring the `mcp` subcommand into a Claude client, see
[`claude-integration.md`](claude-integration.md).
