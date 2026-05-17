# CLI

`local-rag` ships a small CLI that indexes, searches, and runs the MCP server
directly. The same operations are exposed over MCP for client use.

```bash
uv run local-rag index              # incremental reindex (SHA-256 file hashes)
uv run local-rag index vault        # just one source
uv run local-rag search "RRF tuning"
uv run local-rag list               # source name + chunk count
uv run local-rag mcp                # MCP server on stdio
```

All commands honor `--config <path>` or the `LOCAL_RAG_CONFIG` env var; see
[`configuration.md`](configuration.md) for the config schema.

For wiring the `mcp` subcommand into a Claude client, see
[`claude-integration.md`](claude-integration.md).
