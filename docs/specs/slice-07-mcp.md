# Slice 07 — MCP server

Final slice. Expose `search`, `list_sources`, and `index_status` over the
Model Context Protocol on stdio, so Claude Code and Cowork can call them
once `local-rag mcp` is registered.

## Goal

Implement `local_rag.mcp_server` (the tool handlers + FastMCP wiring) and
wire `local-rag mcp` into the CLI subparser.

## Success criteria

### Tools exposed

All take JSON-serializable args/returns. `FastMCP.tool()` decorator infers
schemas from the function signature + type hints.

```python
def search(
    query: str,
    sources: list[str] | None = None,
    k: int = 10,
) -> list[SearchResult]:
    """Hybrid (vector + BM25) search across one or more indexed sources."""


def list_sources() -> list[SourceInfo]:
    """Return every source currently in the store, with chunk counts."""


def index_status() -> IndexStatus:
    """Total chunk count plus per-source breakdown."""
```

Return shapes (TypedDict for JSON serialization):

```python
class SearchResult(TypedDict):
    score: float
    source_name: str
    source_path: str
    heading_path: str
    char_start: int
    char_end: int
    text: str


class SourceInfo(TypedDict):
    name: str
    chunk_count: int


class IndexStatus(TypedDict):
    total_chunks: int
    sources: list[SourceInfo]
```

### Server module (`local_rag.mcp_server`)

- `build_tools(store: Store, embedder: _CLIEmbedder, configured_sources: list[str]) -> Tools` — pure-Python factory returning a `dataclass` with three callables (search, list_sources, index_status). Easy to test without spinning up the MCP transport.
- `make_server(tools: Tools) -> FastMCP` — wires the callables to `FastMCP.tool()` decorators. Server name: `"local-rag"`. Instructions: a one-liner describing what the server provides.
- `run_stdio(config: Config) -> None` — production entry: loads the embedder, runs `health_check()`, builds tools, and calls `server.run()` with `transport="stdio"` (blocks until the client disconnects).

### CLI wiring

`local-rag mcp` → adds a subparser with no arguments, dispatches to a new
`_cmd_mcp(config: Config, store: Store) -> int`. The CLI:

- Builds the embedder via the existing `_build_embedder` factory.
- Calls `health_check()` — same exit-3 behavior as `index`/`search`.
- Hands off to `run_stdio` (which blocks).
- Returns 0 on clean shutdown.

### Stdio safety (critical)

`stdout` is reserved for the MCP JSON-RPC stream. Any extraneous write to
stdout corrupts the protocol. The existing structlog setup in `cli.py`
already routes log lines to stderr, so this is preserved. The `mcp`
subcommand must not call `print()` anywhere on the user-facing path before
or during the MCP loop.

### Behavioral rules

- Unknown source names in `search(sources=...)` are silently ignored (the
  MCP client should not see an error mid-conversation — a missing source
  becomes "no hits from that source"). This is a deliberate divergence from
  the CLI's exit-2 strictness.
- `k` is clamped to `[1, 100]` defensively (an LLM client could pass a wild
  value).
- `search` returns `[]` on empty / whitespace query, no error.
- Tools are sync from FastMCP's perspective; the heavy I/O lives in the
  store and embedder, which are themselves sync. (Future: `asyncio.to_thread`
  if a real workload bottlenecks.)

## Non-goals

- No SSE / streamable-HTTP transport — stdio only (per project spec).
- No authentication. stdio MCP runs in-process with the client.
- No streaming responses. One JSON payload per tool call.
- No prompts/resources — only tools.
- No retries / circuit-breaking around the embedder. One shot per call.
- No live reindex from inside the MCP server. Reindex stays a CLI op.
- No `index_status` mtime / "last reindex at" fields — just counts in v1.

## Files

- `src/local_rag/mcp_server.py` (new) — tools factory + FastMCP wiring.
- `src/local_rag/cli.py` — add `mcp` subparser + `_cmd_mcp` dispatch.
- `tests/test_mcp_server.py` (new) — exercises the tool functions directly
  (FakeEmbedder + tmp_path DB), no transport.
- `tests/test_cli.py` — add one test that `local-rag mcp` is wired (no full
  loop; just verify the dispatch reaches `run_stdio` via monkey-patch).

## Tests

Tools (called as plain functions):
- `list_sources` empty → `[]`.
- `list_sources` after indexing two sources → both with correct counts.
- `index_status` empty → `total_chunks=0, sources=[]`.
- `index_status` reflects per-source counts.
- `search` returns hits after indexing — shape matches `SearchResult` keys
  and values match the underlying `SearchHit`.
- `search` empty query → `[]`.
- `search` with unknown source name in `sources=` → no error; returns hits
  from the valid sources only.
- `search` clamps `k`: `k=0` → uses 1; `k=500` → uses 100.

CLI:
- `local-rag mcp` dispatches to `run_stdio` (monkey-patched in the test).
- Health-check failure → exit 3 before the MCP loop is entered.

## Verification

```
uv run pytest tests/
uv run ruff check src/ tests/
uv run mypy src/
uv run local-rag mcp --help   # smoke: subparser registers
```
