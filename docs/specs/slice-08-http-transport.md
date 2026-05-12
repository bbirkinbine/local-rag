# Slice 08 — HTTP transport for the MCP server

Eighth slice. Add a Streamable-HTTP transport mode to `local-rag mcp` so
Claude Cowork (and any other MCP client that takes a URL instead of a
stdio command) can connect. stdio stays the default. The implementation
reuses the existing tools and tool handlers verbatim — only the
transport changes.

## Goal

Add `local-rag mcp --transport http` that binds a streamable-HTTP MCP
endpoint on `127.0.0.1:<port>`, optionally guarded by a bearer token, and
serves the same three tools (`search`, `list_sources`, `index_status`).

## Success criteria

### CLI surface

```
local-rag mcp                                            # stdio (default — unchanged)
local-rag mcp --transport http                            # 127.0.0.1:8765, no auth
local-rag mcp --transport http --port 9000                # custom port
local-rag mcp --transport http --host 127.0.0.1 \
              --port 8765 --token "shared-secret"         # with bearer auth
```

New `mcp` subcommand flags:

- `--transport {stdio,http}` — default `stdio`.
- `--host HOST` — default `127.0.0.1`. **`0.0.0.0` requires `--token`** (the CLI exits 2 with a clear message if `--host` is not loopback and `--token` is unset; we refuse to bind unauthenticated to the world).
- `--port INT` — default `8765`.
- `--token STRING` — bearer token required on every request. Falls back to the `LOCAL_RAG_MCP_TOKEN` env var (preferred — `ps` reveals CLI args).

stdio mode ignores all the new flags.

### Server module additions (`local_rag.mcp_server`)

```python
def run_http(
    config: Config,
    store: Store,
    embedder: _Embedder,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    token: str | None = None,
) -> None:
    """Start the MCP server over Streamable HTTP. Blocks until SIGINT/SIGTERM.

    Health-checks the embedder before binding (so a broken Ollama exits before
    we open the port). If `token` is provided, every incoming request must
    carry `Authorization: Bearer <token>` or it receives a 401.
    """
```

Implementation:

1. `embedder.health_check()` — same as `run_stdio`.
2. `build_tools(...)` + `make_server(...)` — reuse.
3. `app = server.streamable_http_app()` — Starlette ASGI app.
4. If `token` is set, wrap `app` in a pure-ASGI bearer-auth middleware.
5. `uvicorn.run(app, host=host, port=port, log_config=None)` — blocking.

The bearer-auth middleware is a small ASGI function (no Starlette
`BaseHTTPMiddleware` indirection): for `scope["type"] == "http"`, read the
`Authorization` header, compare via `hmac.compare_digest`, and either pass
through to the wrapped app or send a 401 JSON response.

### Behavioral rules

- **Default binding is loopback only** (`127.0.0.1`). Cowork runs on the
  same Mac, so loopback is sufficient.
- **Non-loopback bind requires a token.** If `--host` is anything other
  than `127.0.0.1`, `localhost`, or `::1`, the CLI exits 2 unless
  `--token` (or `LOCAL_RAG_MCP_TOKEN`) is set. This is the "don't
  accidentally expose your vault" guard.
- **Constant-time token comparison.** `hmac.compare_digest`, not `==`.
- **No token logged.** structlog log lines include host/port but never
  the token value.
- **Server logs to stderr** like the stdio path. uvicorn's own access
  log goes through the structlog factory via `log_config=None` (uvicorn
  defaults route to stderr).
- **Ctrl-C cleanly exits** with no traceback (uvicorn handles SIGINT).

### Don't-touch

- stdio transport behavior must not change. Existing tests stay green.
- The three tool functions (`search`, `list_sources`, `index_status`)
  are untouched — same code path used by both transports.
- No new third-party deps: uvicorn + starlette arrive transitively via
  the `mcp` SDK; we use them but don't add them to `pyproject.toml`.

## Non-goals

- No OAuth, no JWT, no token rotation — single shared secret only.
- No HTTPS / TLS termination. If you need TLS for a non-loopback bind,
  put a reverse proxy in front. (Loopback doesn't need TLS.)
- No rate limiting.
- No CORS — Cowork doesn't need it for streamable HTTP.
- No tunneling integration (ngrok / cloudflared). Out of scope.
- No `[mcp]` section in `config.toml`. CLI flags + env var only — keeps
  the config file focused on what's indexed, not how it's served.

## Files

- `src/local_rag/mcp_server.py` — add `run_http` + the middleware helper.
- `src/local_rag/cli.py` — extend the `mcp` subparser; add the
  loopback-vs-token guard; route to `run_http` when `--transport http`.
- `tests/test_mcp_server.py` — middleware unit tests (pure ASGI scope
  fixtures; no real server).
- `tests/test_cli.py` — dispatch tests (monkey-patch `run_http`); the
  loopback-requires-token guard test.
- `docs/specs/slice-08-http-transport.md` — this file.

## Tests

Middleware (pure ASGI, no network):

- No `Authorization` header → 401, body mentions "bearer".
- Wrong scheme (`Basic xyz`) → 401.
- Wrong token → 401.
- Correct token → passes through to the wrapped app.
- Token comparison is constant-time (we use `hmac.compare_digest`; assert
  the import / call path rather than try to measure timing).

CLI dispatch:

- `local-rag mcp` (no flags) → calls `run_stdio` (unchanged).
- `local-rag mcp --transport http` → calls `run_http` with defaults
  (`127.0.0.1`, `8765`, no token).
- `local-rag mcp --transport http --port 9000 --token T` → `run_http`
  receives those values.
- `LOCAL_RAG_MCP_TOKEN` env var feeds the token when `--token` is absent.
- `local-rag mcp --transport http --host 0.0.0.0` (no token) → exit 2;
  stderr explains why.
- `local-rag mcp --transport http --host 0.0.0.0 --token T` → allowed.
- `--transport http` still runs the embedder health-check (failure →
  exit 3, as today).

## Verification

```
uv run pytest tests/           # 159 prior + ~10 new
uv run ruff check src/ tests/
uv run mypy src/               # strict
# Smoke test (manual, optional):
uv run local-rag mcp --transport http --port 8765 &
curl -s http://127.0.0.1:8765/mcp -H 'Accept: application/json,text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
kill %1
```
