# Integrating local-rag with Claude apps

`local-rag` exposes three MCP tools (`search`, `list_sources`,
`index_status`) over two transports (stdio, Streamable HTTP). What
works where depends on the client.

## Capability matrix

| Client                       | Transports it accepts   | Recommended            | local-rag status                     |
|------------------------------|-------------------------|------------------------|--------------------------------------|
| Claude Code (CLI + VS Code)  | stdio, HTTP             | **stdio**              | ✓ Fully supported                    |
| Claude Cowork (desktop)      | HTTP only               | **HTTP (loopback)**    | ✓ Fully supported                    |
| Claude.ai (web)              | HTTP only, public URL   | none                   | ✗ Not recommended (see below)        |

The rest of this doc walks each one. Pick the section that matches your
client.

---

## Claude Code (CLI and VS Code)

Stdio is the right transport here — Claude Code auto-spawns the server
per session, no separate process to keep alive.

### Setup (one-time)

```bash
claude mcp add local-rag -- uv --directory /path/to/local-rag run local-rag mcp
```

Replace `/path/to/local-rag` with the absolute path to your clone.

### Verify

```bash
claude mcp list
# Expected: local-rag: uv --directory ... run local-rag mcp - ✓ Connected
```

If you see `✗ Failed to connect`, your config file is probably missing
or unparseable. Run `uv run local-rag list` directly — same config-load
path, but with a clearer error message.

### Using it

Restart Claude Code (or open a new chat) after `mcp add`. The three tools
appear in Claude's tool list automatically; the model decides when to
call them based on your prompts. Prompts that tend to trigger a call:

- "Search my vault for notes on RRF tuning" → `search`
- "What's indexed in local-rag?" → `list_sources` or `index_status`
- "Find my notes about consulting upsell strategy" → `search`

Prompts that won't: ones that look answerable from the current
working-directory context alone (Claude Code already has `Grep`/`Read`
over the CWD). Naming `local-rag` or `vault` in the prompt nudges the
model toward the right tool.

---

## Claude Cowork (desktop)

Cowork's connector layer only accepts remote MCP (HTTP / Streamable
HTTP), not stdio. So you run `local-rag` as a long-lived HTTP server and
point Cowork at the URL.

### Setup

Cowork requires `https://` URLs even for loopback — it rejects plain
HTTP. You'll need a locally-trusted TLS cert; see
[tls-setup.md](tls-setup.md) for the full mkcert walkthrough, cert
rotation, and verification commands. Quick version:

```bash
brew install mkcert
mkcert -install
mkdir -p ~/.config/local-rag && cd $_
mkcert localhost 127.0.0.1 ::1
```

**1. Run the server with TLS.** For a quick test:

```bash
uv run local-rag mcp --transport http --port 8765 \
  --cert ~/.config/local-rag/localhost+2.pem \
  --key  ~/.config/local-rag/localhost+2-key.pem
```

For "always-on" (recommended), see
[deployment.md](deployment.md#http-server-lifetime) — `launchd`
LaunchAgent is the Mac-native choice; auto-starts at login, restarts on
crash.

**2. Add it in Cowork.** Cowork's MCP settings panel (location depends on
your build — usually Settings → MCP / Connectors → Add custom server)
takes:

- **URL**: `https://localhost:8765/mcp`
- **Auth**: none required for the loopback default

**3. Restart Cowork** so the new connector is loaded.

### Security defaults

The server binds to `127.0.0.1` only — reachable from Cowork on the same
Mac, not from anywhere else. If you'd rather run the server on a
different machine (LAN), you'll need a bearer token; see
[README.md](../README.md#claude-cowork) and
[deployment.md](deployment.md#with-a-bearer-token).

### Verify

```bash
lsof -i :8765
# Expected: a Python process LISTENing on the configured port
```

Then in Cowork, ask something the model would route to local-rag:

> "Use local-rag to tell me what sources are indexed."

If the model calls `list_sources` you'll see the results. If it doesn't
call any tool, name `local-rag` explicitly — Cowork's tool-selection
heuristics vary by build.

### Stopping it

```bash
# If you ran it via launchd:
launchctl unload ~/Library/LaunchAgents/com.bbirkinbine.local-rag-mcp.plist

# If you ran it ad-hoc:
lsof -i :8765         # find PID
kill <PID>
```

---

## Claude.ai (web)

Short answer: **not recommended**. The web app accepts remote MCP servers
via custom connectors, but the URL has to be reachable over the public
internet. `local-rag` is local-first by design — exposing it to the
internet violates the privacy model the project exists to preserve.

### What it would take (and why we don't document the specifics)

You'd need:

1. A tunnel (ngrok, cloudflared, Tailscale Funnel) pointing at your
   local `local-rag` HTTP server.
2. A strong bearer token configured into both the server and the
   connector.
3. The tunnel provider sees the encrypted traffic only if you're using a
   real tunnel-with-TLS — many free tiers terminate TLS at their edge,
   meaning they can read every query and every result.

That last point is the killer. The project rule is "no cloud APIs, no
API keys, no telemetry"; routing your private vault through ngrok's
edge is a category of "cloud API" we explicitly avoid.

### What to use instead on web

For the web client, the alternative is claude.ai's own **Projects**
feature with attached knowledge files. Trade-offs vs local-rag:

| Capability                    | local-rag             | claude.ai Projects     |
|-------------------------------|-----------------------|------------------------|
| Privacy                       | Stays on your machine | Uploaded to Anthropic  |
| Document count                | Tens of thousands     | ~30 attached files     |
| Semantic + keyword search     | Yes (hybrid RRF)      | Limited                |
| Cross-repo / vault            | Yes                   | One project at a time  |
| Incremental updates           | SHA-256 skip          | Re-upload manually     |

If web access matters more than privacy for a specific session, attach a
few key docs to a Project. Otherwise, save the deeper queries for Claude
Code / Cowork where local-rag is wired up.

---

## How tool calls actually work

You don't invoke tools directly. The model decides whether and when to
call `search` / `list_sources` / `index_status` based on the
conversation, the system prompt the client uses, and the tool
descriptions in [mcp_server.py](../src/local_rag/mcp_server.py#L120).

### Prompts that tend to trigger a search

- Verbs: "find", "search", "look up", "show me"
- Mentioning the source by name: "in my vault", "in the local-rag repo"
- Mentioning local-rag itself: "use local-rag to..."
- Specific knowledge you'd have written down: file names, project
  codenames, technical terms unique to your notes

### Prompts that won't

- General coding questions answerable from Claude's training
- Questions about files already in the current Claude Code working
  directory (Claude Code prefers `Grep`/`Read` over MCP search for
  in-CWD lookups)
- Vague meta-questions ("what do you know?") — these rarely trigger
  tool calls anywhere

---

## Troubleshooting

**Claude Code: `claude mcp list` shows `✗ Failed to connect`** —
Usually a missing or invalid config. `uv run local-rag list` from your
shell uses the same config loader and prints a friendlier error.

**Claude Code: tools don't show up in a chat** — Restart Claude Code.
MCP servers are loaded at session start; `mcp add` doesn't retroactively
inject into running sessions.

**Cowork: connector status flips between "Connected" and "Failed"** —
The server died or wasn't running when Cowork tried to reconnect. Check
`~/.local/state/local-rag/http.log` (if you used the launchd setup from
[deployment.md](deployment.md)) for the underlying error.

**Cowork: tools register but every call returns "embedder unreachable"
— Ollama isn't running on the URL in your config, or doesn't have
`bge-m3` pulled. `ollama list` and `curl
http://localhost:11434/api/tags` are the usual diagnostics.

**Any client: empty results for every query** — Confirm the index ran
and has rows: `uv run local-rag list`. If counts are zero or look wrong,
re-run `uv run local-rag index` and check stderr for errors.

**Any client: stale results after editing the vault** — The HTTP server
reads a fresh table snapshot per query, so results reflect indexed
state, not disk state. Re-run `uv run local-rag index` (or wait for the
next cron / launchd tick) to refresh.
