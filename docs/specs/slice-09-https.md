# Slice 09 — HTTPS for the HTTP transport

Ninth slice. Claude Cowork (and probably other future MCP clients)
rejects plain-HTTP MCP connectors and requires `https://` URLs even for
loopback. Add a TLS termination path so `local-rag mcp --transport http`
can serve HTTPS directly via uvicorn.

## Goal

Add `--cert` / `--key` flags to `local-rag mcp --transport http`. When
both are provided, uvicorn binds with TLS and the connector URL becomes
`https://127.0.0.1:<port>/mcp`. When neither is provided, behavior is
unchanged (plain HTTP). Partial config (one but not the other) is a CLI
error.

## Success criteria

### CLI surface

```
local-rag mcp --transport http \
              --cert /path/to/cert.pem \
              --key  /path/to/key.pem \
              --port 8765
```

New `mcp` subcommand flags (only meaningful for `--transport http`):

- `--cert PATH` — TLS certificate file (PEM).
- `--key PATH` — TLS private key file (PEM).

Stdio mode ignores them.

### Validation rules

- Both flags must be set together. `--cert` without `--key` (or vice
  versa) → exit 2 with a clear stderr message.
- Both files must exist and be readable → exit 2 if not (so launchd
  doesn't restart-loop on a typo).
- If neither flag is set, the server runs plain HTTP as today
  (preserving slice 8 behavior).

### Server module changes (`local_rag.mcp_server`)

```python
def run_http(
    config: Config,
    store: Store,
    embedder: _Embedder,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    token: str | None = None,
    cert_file: Path | None = None,
    key_file: Path | None = None,
) -> None:
```

When both are set, `uvicorn.run(..., ssl_keyfile=str(key_file),
ssl_certfile=str(cert_file))`. structlog logs the cert path (not the
key path) and `tls=true` for visibility.

### Behavioral rules

- Cert paths logged; key path **not** logged (defense-in-depth in case
  logs leak via screen-share).
- The embedder health-check still runs before binding — same as slice 8.
- The loopback-vs-token guard still applies. Token + TLS is the
  belt-and-suspenders setup for non-loopback binds.
- No auto-cert-generation. We don't invoke `openssl` or anything else —
  the user supplies the files. (Documenting `mkcert` in
  [deployment.md](../deployment.md) and
  [claude-integration.md](../claude-integration.md) is enough.)

## Non-goals

- No ACME / Let's Encrypt integration. The cert is user-supplied.
- No mutual TLS (client certs). Bearer token + server TLS is the auth
  model.
- No automatic redirect from `http://` to `https://`. uvicorn binds one
  protocol at a time.
- No config-file fields (`[mcp]` block). CLI flags + env vars only —
  same rationale as slice 8.
- No support for cert/key passed as raw PEM in env vars. Files only.

## Files

- `src/local_rag/mcp_server.py` — extend `run_http` signature; pass
  through to `uvicorn.run`.
- `src/local_rag/cli.py` — new `--cert` / `--key` flags; partial-config
  + file-readability guards; pass through to `run_http`.
- `tests/test_cli.py` — dispatch + guard tests.
- `docs/specs/slice-09-https.md` — this file.
- `docs/deployment.md` — mkcert section + updated launchd plist.
- `docs/claude-integration.md` — Cowork section: HTTPS is the path
  forward.
- `README.md` — quick-recipes note that Cowork needs HTTPS, point at
  the docs.

## Tests

CLI dispatch:

- `--transport http --cert C --key K` → `run_http` receives both paths.
- `--transport http` alone → `run_http` receives `cert_file=None`,
  `key_file=None` (HTTP, unchanged).
- `--cert C` without `--key` → exit 2.
- `--key K` without `--cert` → exit 2.
- `--cert /nonexistent` (file missing) → exit 2 before reaching
  `run_http`.
- `--key /nonexistent` (file missing) → exit 2.
- Existing tests (stdio default, HTTP defaults, token, loopback guard)
  still pass unchanged.

## Verification

```
uv run pytest tests/           # 171 prior + ~6 new
uv run ruff check src/ tests/
uv run mypy src/               # strict

# Smoke (manual, with mkcert installed):
mkcert -install
mkcert localhost 127.0.0.1 ::1
uv run local-rag mcp --transport http --port 8765 \
  --cert ./localhost+2.pem --key ./localhost+2-key.pem &
curl -sf https://localhost:8765/mcp -H 'Accept: application/json,text/event-stream' \
     -H 'Content-Type: application/json' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  && echo TLS-OK
kill %1
```
