# TODO

## Cowork integration — in flight (slice 10, premise revised 2026-07-08)

Verified against current Anthropic docs (Claude Code plugins reference, Cowork
plugin guide, Claude Desktop local-MCP article). Two corrections to the
slice-10 premise:

- **The plugin scaffolding under `claude-plugin/` is invalid as written.**
  `userConfig` in `plugin.json` and `${user_config.*}` interpolation in
  `.mcp.json` are Claude Desktop *extension* (MCPB) syntax, not part of the
  Claude Code / Cowork plugin schema. Only `${CLAUDE_PLUGIN_ROOT}` and
  `${ENV_VAR}` expansion are supported in a plugin's `.mcp.json`.
- **Cowork has no "point at a folder" install flow.** Plugins must be packaged
  (a git repo with `.claude-plugin/marketplace.json`, an uploaded bundle, or
  admin-provisioned). More importantly, a plugin isn't needed at all:
  **Cowork bridges MCP servers configured in Claude Desktop's
  `claude_desktop_config.json`** (it does not read Claude Code's
  `~/.claude.json`). A plain `mcpServers` stdio entry there is the documented
  path for a local server.

Current working state (verified on this machine 2026-07-09):

- Claude Code: registered at user scope and **verified end-to-end** — the
  three tools are callable from a live VS Code session (`index_status`
  returned 17,443 chunks: vault 17,079 + local-rag 364).
  `claude mcp add -s user local-rag -- uv --directory <repo> run local-rag mcp`
- Cowork: **verified end-to-end 2026-07-09** — a Cowork session called the
  tools against the live index via the `claude_desktop_config.json` stdio
  bridge and stress-tested search quality (see "Ranking quality" below).
  Slice 10 integration goal is met.
- Indexing cadence: launchd job installed and loaded 2026-07-09
  (`~/Library/LaunchAgents/com.bbirkinbine.local-rag-index.plist`, every
  30 min, logs to `~/.local/state/local-rag/index.log`). Recipe in
  `docs/deployment.md` § "Indexing cadence".

### Outstanding

- [x] ~~Confirm the tools are callable from a Cowork chat~~ Done 2026-07-09
      (see current working state above).
- [x] ~~Decide the fate of `claude-plugin/`~~ Deleted 2026-07-09: invalid
      schema, built on a nonexistent install flow, zero consumers now that
      both clients use verified stdio routes, and distribution isn't a repo
      goal. Rationale preserved in `docs/specs/slice-10-cowork-plugin.md`
      and git history.
- [x] ~~Update `README.md` and `docs/claude-integration.md`~~ Done
      2026-07-09: Cowork section now describes the
      `claude_desktop_config.json` route; Claude Code section uses
      `-s user`. Path sweep resolved by decision: the generic
      `~/Downloads/src/local-rag` paths in public docs are placeholder
      clone paths (`git clone` yields a `local-rag` dir), not references
      to this machine — no sweep needed.
- [x] ~~Remove the slice 8/9 HTTP/HTTPS transport~~ Done 2026-07-09:
      `run_http` + bearer-auth middleware, the
      `--transport/--host/--port/--token/--cert/--key` flags, TLS
      validation, and their tests are gone (`cli.py` back under the
      300-line limit); `docs/tls-setup.md` deleted; `docs/deployment.md`
      reduced to indexing cadence. Slice 08/09 specs kept as historical
      record; code recoverable at `a7ab5ee`/`8e53cf4`.
- [x] ~~Refresh the stale "Open work / current state" sections~~ Done
      2026-07-09: `docs/specs/local-rag.md` and `CLAUDE.md` now reflect
      the implemented state, the transport removal, and the verified
      integrations; both point at `TODO.md` as the single open-work
      tracker. CLAUDE.md gained a "never commit personal vault paths"
      rule.

### Don't repeat

Twice now a Cowork integration was built on an unverified premise about how
Cowork ingests servers (the HTTPS connector field; the folder-based plugin
install). Before building for a specific client, verify the ingestion path
against current docs *and* a live install.

## Ranking quality — from Cowork stress-test (2026-07-09)

A Cowork session ran real queries and diagnosed poor ranking. Both root
causes verified against the code:

- **Scores are bare RRF values** (`store.py` `_RRF_K = 60`; every score is a
  sum of `1/(60+rank)`), so `score` carries rank information only — no
  absolute relevance signal, no meaningful threshold, and callers can't
  detect "nothing relevant found".
- **Markdown chunks are effectively unbounded** (`chunkers.py`
  `_MAX_CHUNK_CHARS = 24_000` exists only to protect the embedder; heading
  sections become single chunks). 4.5–8k-char keyword-dense list chunks
  (link/bookmark-audit style notes) reliably place in the BM25 list and
  get fused into the top 5, beating topical notes.

The embedding model is NOT the problem: already `bge-m3` @ 1024-dim — the
strong option the feedback said to benchmark toward. Skip the model swap.

Work items, in order (constraints: keep `search(query, sources, k)`
backward compatible; keep indexing incremental; index-format changes need
a migration path):

- [ ] Eval harness first: golden-query set (query → expected file paths)
      with recall@5 / MRR reporting. Golden queries are inherently personal
      (they name real vault notes), so the harness reads them from a
      gitignored local file with one synthetic example checked in as a
      template. `eval/golden.local.toml` already exists locally, seeded
      with the three real failures from the 2026-07-09 stress-test
      (`*.local.toml` is gitignored).
- [ ] Chunking: cap markdown chunks at ~800–1,200 chars with overlap
      (headings-first split retained). Consider down-weighting or skipping
      chunks that are mostly list items (>70% of lines are bullets).
      Requires reindex; measure with the harness before/after.
- [ ] Score transparency: return raw cosine similarity (the vector leg
      already computes it — `store.py` line ~139) and optionally the BM25
      score alongside the RRF rank, so callers can judge hit strength.
- [ ] Cosine re-scoring of the top ~30 fused candidates as a cheap
      second stage. Note: a local cross-encoder reranker was declared
      closed in the 2026-07-08 design review ("the calling LLM is the
      reranker") — cosine re-scoring is compatible with that decision;
      revisit the cross-encoder only if the harness shows the chunking and
      score-transparency fixes were insufficient.
- [ ] Frontmatter/metadata filters & boosts (tags, folder path, mild mtime
      recency) — only if still needed after the above.

## From design review vs. frontier agentic search (2026-07-08)

Rationale in `docs/specs/local-rag.md` § "Positioning vs. frontier agentic search".

- [ ] Sharpen the MCP server instructions and `search` tool description to state when `search` beats the client's built-in grep: conceptual similarity without shared keywords, and content outside the current project (vault from Claude Code, repos from Cowork). This is the highest-leverage change for tool adoption.
- [ ] Consider an optional neighboring-chunk expansion parameter on `search` (e.g. `context_chunks: int`). Cowork can't read local files, so returned chunk text is all it gets; `chunk_index` already supports this.
- [ ] Hold tree-sitter code chunking until usage shows the tool is actually called for in-repo code search — agentic grep likely covers that case.
- [ ] Treat the deferred cross-encoder reranker as closed: the calling LLM reranks the top-k hits.
