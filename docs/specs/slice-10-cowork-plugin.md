# Slice 10 — Cowork plugin packaging

Tenth slice. Slices 8 and 9 chased the wrong target: Cowork's "Add custom
connector" URL field is the *cloud-brokered remote MCP* path, not a local
one. Even with valid TLS, Anthropic's edge has no route to a loopback
server, so packets never arrive at our box. The stdio install path for
Cowork is not a URL — it is the **plugin** folder uploaded through
**Customize → Plugins**.

Add a `claude-plugin/` directory at the repo root containing a Claude
plugin manifest plus `.mcp.json` that wires Cowork to the existing
`local-rag mcp` stdio entry point.

## Goal

Make local-rag installable in Cowork via Customize → Browse plugins →
upload a custom plugin file. After install, the three MCP tools
(`search`, `list_sources`, `index_status`) appear in Cowork sessions and
run on the user's Mac, with no TLS, no public exposure, and no
cloud-broker hop.

## Success criteria

### Directory layout

```
local-rag/
├── claude-plugin/
│   ├── .claude-plugin/
│   │   └── plugin.json       # plugin manifest
│   └── .mcp.json             # MCP server definitions (stdio)
└── …                         # everything else unchanged
```

The plugin root is `claude-plugin/` so it doesn't shadow the project
root or alter Claude Code's existing `.mcp.json` story (we don't have
one today, but if we add one later it should be scoped to the project,
not to the plugin).

### Plugin manifest

`claude-plugin/.claude-plugin/plugin.json` ships:

- `name`: `"local-rag"` (kebab-case, matches the CLI binary).
- `version`: explicit semver (start at `"0.1.0"`). Per Claude Code's
  plugin docs, an explicit version means users only see updates when we
  bump it — preferred for stability over the commit-SHA fallback.
- `description`, `author`, `keywords` for the catalog.
- `userConfig` with two prompts so the manifest is installable on a
  fresh machine without hand-editing:
  - `repo_path` (`type: directory`, required) — the absolute path to
    the user's local-rag clone, substituted into the `.mcp.json` as
    `${user_config.repo_path}`.
  - `uv_path` (`type: string`, default `"uv"`) — the `uv` binary to
    invoke. Default `"uv"` works when `uv` is on Cowork's PATH; users
    whose Cowork inherits a minimal GUI-launch PATH (no Homebrew)
    override with the absolute path from `which uv` (typically
    `/opt/homebrew/bin/uv`).

### MCP wiring

`claude-plugin/.mcp.json`:

```json
{
  "mcpServers": {
    "local-rag": {
      "command": "${user_config.uv_path}",
      "args": [
        "--directory",
        "${user_config.repo_path}",
        "run",
        "local-rag",
        "mcp"
      ]
    }
  }
}
```

This invokes the same `local-rag mcp` (stdio) entry point Claude Code
already uses. No new server code, no new transport, no Python
changes.

### Behavioral rules

- The plugin **does not** bundle Python source. It points at the user's
  existing clone via `userConfig.repo_path`. The source repo stays the
  single source of truth.
- The plugin **does not** ship `--transport http` flags. Cowork's
  plugin runtime is stdio-only by design; the HTTP path stays available
  via the CLI for non-Cowork use cases (Tailscale Serve on a LAN box,
  Claude Code over a remote tunnel, etc.).
- The plugin **does not** carry skills, agents, hooks, or commands in
  v1. Just the MCP server. Skill packaging can come later if useful.

## Non-goals

- **No `.mcpb` bundle.** The MCPB path requires either a Node-based
  server or a Python-runtime bundling story; we don't need it for our
  own install, and we can revisit if/when we want one-click public
  distribution.
- **No marketplace listing.** Personal-use scope only. If we ever
  publish, that's a separate slice that adds `marketplace.json` and the
  submission flow.
- **No auto-detection of `uv` path or repo path.** The user supplies
  both at install time via the Cowork UI. Avoiding magic keeps the
  failure modes obvious.
- **No changes to the Python source.** This is packaging-only.
- **No changes to existing slice-08/09 HTTP/HTTPS code.** That code is
  still correct for the LAN/tunnel use case it actually supports — see
  the doc rework in this slice's "Files" list.

## Files

- `claude-plugin/.claude-plugin/plugin.json` — manifest.
- `claude-plugin/.mcp.json` — MCP server config.
- `docs/specs/slice-10-cowork-plugin.md` — this file.

Doc updates queued for a follow-up commit (not part of this slice so
the diff stays minimal):

- `README.md` — the "Claude Cowork (desktop) — HTTPS" recipe is wrong.
  Replace with the plugin-upload recipe.
- `docs/claude-integration.md` — same fix in the Cowork section; the
  capability matrix row should read "stdio via plugin (recommended)"
  rather than "HTTPS (loopback)".
- `docs/tls-setup.md` — keep, but reframe scope at the top: TLS is
  only needed for the LAN/tunnel use case, not for Cowork.
- `docs/specs/slice-09-https.md` — mark superseded for the Cowork
  context; the HTTPS code still serves the tunnel path.

## Tests

No pytest changes. This is packaging only — there is no Python code
to test. Verification is manual.

Manual checks (run from the repo root):

1. `python -c "import json; json.load(open('claude-plugin/.claude-plugin/plugin.json'))"` — JSON parses.
2. `python -c "import json; json.load(open('claude-plugin/.mcp.json'))"` — JSON parses.
3. `claude plugin validate ./claude-plugin` (Claude Code's validator,
   if available locally) — schema clean. If `claude` CLI isn't
   convenient, the parse checks above are enough; Cowork's installer
   will surface schema errors directly at upload time.

## Verification

Smoke test in Cowork (manual, one-time):

1. Build the plugin folder bundle locally:
   `(cd claude-plugin && zip -r ../local-rag-plugin.zip .)`
2. In Cowork: **Customize → Plugins → Upload custom plugin file** →
   pick `local-rag-plugin.zip`.
3. When prompted, fill in `repo_path` (`/Users/brian/Downloads/src/local-rag`)
   and `uv_path` (start with `uv`; switch to `/opt/homebrew/bin/uv` if
   step 5 fails).
4. Start a fresh Cowork conversation. Ask: *"Use local-rag to list
   indexed sources."*
5. Expected: Claude calls `list_sources` and reports the source
   names + chunk counts. If instead you get "embedder unreachable",
   Ollama isn't running. If you get nothing at all, check
   `~/Library/Logs/Claude/` for the spawn error — most likely a `uv`
   PATH issue, fix via the `uv_path` override.

Reference docs used while writing this slice:

- [Plugins reference (claude-code)](https://code.claude.com/docs/en/plugins-reference)
- [Use plugins in Claude Cowork](https://support.claude.com/en/articles/13837440-use-plugins-in-claude-cowork)
- [Get started with custom connectors using remote MCP](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp) (for the explicit "cloud-brokered" rule we previously missed).
