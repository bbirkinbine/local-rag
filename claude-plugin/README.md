# Cowork plugin

Scaffolding for installing `local-rag` into Cowork (desktop) as a plugin. The
plugin exposes the same three MCP tools (`search`, `list_sources`,
`index_status`) over stdio.

**Status:** not yet verified end-to-end. See [`../TODO.md`](../TODO.md).

## Layout

- `.claude-plugin/plugin.json` — Cowork plugin manifest (name, version, user
  config knobs for the repo path and the `uv` binary).
- `.mcp.json` — MCP server wiring; spawns `uv run local-rag mcp` over stdio.

## Install

The intended path is Cowork → Customize → Plugins, pointing at this folder.
Design and rationale (including why the HTTP/HTTPS transport route doesn't
work for Cowork) live in
[`../docs/specs/slice-10-cowork-plugin.md`](../docs/specs/slice-10-cowork-plugin.md).
