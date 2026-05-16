# TODO

## Cowork integration — in flight (slice 10)

The Cowork path is **not yet verified end-to-end**. Status as of the last commit on `main` (`c339a9e`):

- Slices 8 and 9 added HTTP/HTTPS transport on the assumption that Cowork's "Add custom connector" URL field accepted local servers. It does not — that field is the cloud-brokered remote-MCP path, and Anthropic's edge has no route to a loopback host, so packets never arrive.
- Slice 10 pivoted to the documented local install path: Customize → Plugins, pointing at a folder with a `.claude-plugin/plugin.json` manifest and a `.mcp.json` that wires a stdio command. Scaffolding is in place under `claude-plugin/` and the spec is at `docs/specs/slice-10-cowork-plugin.md`.

### Outstanding

- [ ] Install the plugin into Cowork end-to-end and confirm `search` / `list_sources` / `index_status` tools are reachable from a Cowork chat.
- [ ] Update `README.md` and `docs/claude-integration.md` to point Cowork users at the plugin path (currently they still describe the HTTP-connector route).
- [ ] Decide whether to keep, deprecate, or remove the slice 8/9 HTTP/HTTPS transport now that the documented Cowork path no longer needs it. Claude Code in VS Code uses stdio too, so HTTP has no remaining first-party consumer — but it may still be useful for other MCP clients.

### Don't repeat

The HTTP/HTTPS work was built on an incorrect premise about Cowork's connector field. Before adding a transport to satisfy a specific client in future, verify the client actually routes to it.
