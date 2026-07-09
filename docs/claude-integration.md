# Integrating local-rag with Claude apps

`local-rag` exposes three MCP tools (`search`, `list_sources`,
`index_status`) over a stdio MCP server. Both first-party clients spawn
that server themselves — there is no long-running process to manage.

## Capability matrix

| Client                       | How it reaches local-rag                          | local-rag status |
|------------------------------|---------------------------------------------------|------------------|
| Claude Code (CLI + VS Code)  | stdio, registered via `claude mcp add`            | ✓ Supported      |
| Claude Cowork (desktop)      | stdio, via `claude_desktop_config.json`           | ✓ Supported      |
| Claude.ai (web)              | remote MCP only (public HTTPS URL)                | ✗ Not supported  |

An HTTP/HTTPS transport existed in earlier revisions but was removed once
both first-party clients settled on stdio. If some other MCP client needs
HTTP, front the stdio server with a generic adapter such as `mcp-proxy`.

---

## Claude Code (CLI and VS Code)

Claude Code auto-spawns the server per session; nothing to keep alive.

### Setup (one-time)

```bash
claude mcp add -s user local-rag -- uv --directory /path/to/local-rag run local-rag mcp
```

Replace `/path/to/local-rag` with the absolute path to your clone.
`-s user` registers the server at user scope so it's available in every
project, not just the directory you happened to run the command from.

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

Cowork bridges MCP servers configured in Claude Desktop's
`claude_desktop_config.json` — it does **not** read Claude Code's
`~/.claude.json`, and it has no folder-based plugin install. A plain
`mcpServers` stdio entry in the desktop config is the documented path
for a local server.

### Setup

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(create it if it doesn't exist) and add:

```json
{
  "mcpServers": {
    "local-rag": {
      "command": "/opt/homebrew/bin/uv",
      "args": [
        "--directory",
        "/path/to/local-rag",
        "run",
        "local-rag",
        "mcp"
      ]
    }
  }
}
```

Two path gotchas:

- **Use an absolute path to `uv`.** GUI apps don't inherit your shell's
  PATH. `which uv` tells you where yours is — commonly
  `/opt/homebrew/bin/uv` (Apple Silicon Homebrew) or
  `~/.local/bin/uv` (the uv installer; spell out `/Users/<you>/...`).
- **Use the absolute path to your clone** for `--directory`.

### Verify from a chat

Fully restart the Claude desktop app (quit, not just close the window)
so the new server is loaded. Then in a Cowork chat, ask something the
model would route to local-rag:

> "Use local-rag to tell me what sources are indexed."

If the model calls `list_sources` you'll see the results. If it doesn't
call any tool, name `local-rag` explicitly — tool-selection heuristics
vary by build.

---

## Claude.ai (web)

Short answer: **not supported**. The web app only accepts remote MCP
servers reachable over the public internet. `local-rag` is local-first
by design — tunneling your private vault through a public endpoint
violates the privacy model the project exists to preserve, so we don't
document a route.

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
descriptions in [mcp_server.py](../src/local_rag/mcp_server.py).

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

**Cowork: server doesn't appear after editing the config** — Fully quit
and relaunch the desktop app; the config is read at startup. Then check
the JSON parses (`python3 -m json.tool < .../claude_desktop_config.json`).

**Cowork: server listed but fails to start** — Almost always the `uv`
path. GUI apps don't inherit shell PATH, so `"command": "uv"` fails
even though `uv` works in your terminal; use the absolute path from
`which uv`.

**Any client: every call returns "embedder unreachable"** — Ollama
isn't running on the URL in your config, or doesn't have `bge-m3`
pulled. `ollama list` and `curl http://localhost:11434/api/tags` are
the usual diagnostics.

**Any client: empty results for every query** — Confirm the index ran
and has rows: `uv run local-rag list`. If counts are zero or look wrong,
re-run `uv run local-rag index` and check stderr for errors.

**Any client: stale results after editing the vault** — The server
reads a fresh table snapshot per query, so results reflect indexed
state, not disk state. Re-run `uv run local-rag index` (or set up a
schedule; see [deployment.md](deployment.md)) to refresh.
