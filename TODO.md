# TODO

## Open — ranking quality

Search works end-to-end from both clients, but a Cowork stress-test
(2026-07-09, real queries against the live index) showed ranking quality
is poor. Two root causes, both verified in the code:

- **Scores carry no relevance signal.** `search` returns bare RRF values
  (`store.py`, `_RRF_K = 60`): every score is a sum of `1/(60+rank)`, so
  it encodes rank only. Callers can't tell a strong hit from a weak one,
  can't threshold, and can't detect "nothing relevant found".
- **Markdown chunks are effectively unbounded.** The only cap
  (`chunkers.py`, `_MAX_CHUNK_CHARS = 24_000`) protects the embedder, not
  retrieval — a heading section becomes one chunk. Keyword-dense 4.5–8k
  char list chunks (bookmark-audit style notes) reliably place in the
  BM25 list and get fused into the top 5, beating topical notes.

The embedding model is not the problem (already `bge-m3` @ 1024-dim).

Constraints for all items: keep `search(query, sources, k)` backward
compatible, keep indexing incremental, and give any index-format change a
migration path.

In priority order:

- [x] **Eval harness** (done 2026-07-09) — `local-rag eval` runs a
      golden-query set against the live index and reports file-level
      recall@5 / MRR (expected paths suffix-matched on path-component
      boundaries). Template checked in at `eval/golden.example.toml`;
      real data stays in gitignored `eval/golden.local.toml`.
      **Baseline: recall@5 = 0.000, MRR = 0.000** on the three
      stress-test queries — all top-5 hits were code chunks from the
      `local-rag` repo itself, confirming the ranking diagnosis above.
- [ ] **Cap markdown chunk size** at ~800–1,200 chars with overlap,
      keeping the headings-first split. Consider down-weighting or
      skipping chunks that are mostly list items (>70% of lines are
      bullets). Requires a reindex; measure before/after with the harness.
- [ ] **Expose real scores** — return raw cosine similarity (the
      vector-only path already computes it in `store.py`) and optionally
      the BM25 score alongside the RRF rank, so callers can judge hit
      strength.
- [ ] **Cosine re-scoring** of the top ~30 fused candidates as a cheap
      second stage — only if the harness shows the two fixes above were
      insufficient.
- [ ] **Frontmatter filters & boosts** (tags, folder path, mild mtime
      recency) — only if still needed after the above.

## Open — tool adoption

From the 2026-07-08 design review (rationale in `docs/specs/local-rag.md`
§ "Positioning vs. frontier agentic search"):

- [ ] **Sharpen the MCP server instructions and `search` description** to
      state when `search` beats the client's built-in grep: conceptual
      similarity without shared keywords, and content outside the current
      project (vault from Claude Code, repos from Cowork). Highest-leverage
      change for tool adoption; pairs naturally with the score-transparency
      item (describe what the score means once it's a real similarity).
- [ ] **Consider a neighboring-chunk expansion parameter** on `search`
      (e.g. `context_chunks: int`). Cowork can't read local files, so the
      returned chunk text is all it gets; `chunk_index` already supports
      this.

## Decided / on hold

- **Embedding model swap: not needed.** Already `bge-m3`; revisit only if
  the eval harness shows the ranking fixes were insufficient (a swap
  forces a full reindex).
- **Local cross-encoder reranker: closed.** The calling LLM reranks the
  top-k hits (2026-07-08 design review). Cosine re-scoring above is
  compatible with this; the cross-encoder only comes back if the harness
  proves everything cheaper insufficient.
- **Tree-sitter code chunking: on hold** until usage shows the tool is
  actually called for in-repo code search — agentic grep likely covers it.

## Lessons

- **Verify a client's ingestion path against current docs *and* a live
  install before building for it.** Two Cowork integrations were built on
  unverified premises (the HTTPS connector field; a folder-based plugin
  install that doesn't exist) and both were thrown away.
- **Never commit personal vault paths or note names** — this is a public
  repo. Golden-query eval data stays in gitignored `*.local.toml` files.

## Done (2026-07-09)

Condensed record; details in git history and `docs/specs/`.

- [x] **Verify both client integrations end-to-end.** Claude Code:
      `claude mcp add -s user local-rag -- uv --directory <repo> run
      local-rag mcp`. Cowork: stdio `mcpServers` entry in
      `claude_desktop_config.json` (absolute `uv` path — GUI apps don't
      inherit shell PATH). A Cowork session exercised all three tools
      against the live index.
- [x] **Set up scheduled indexing** — launchd job every 30 min
      (`~/Library/LaunchAgents/com.bbirkinbine.local-rag-index.plist`,
      logs to `~/.local/state/local-rag/index.log`). Recipe in
      `docs/deployment.md`.
- [x] **Remove the HTTP/HTTPS transport** (was slices 08–09). No
      consumers — both clients use stdio. Recoverable at
      `a7ab5ee`/`8e53cf4`; an adapter like `mcp-proxy` covers any future
      HTTP-only client.
- [x] **Delete the `claude-plugin/` scaffolding** (was slice 10). Invalid
      schema, built for an install flow Cowork doesn't have, and
      unnecessary — the desktop-config route above is the documented
      path. Distribution isn't a repo goal.
- [x] **Bring the docs current** — `claude-integration.md` and
      `deployment.md` rewritten around stdio; `tls-setup.md` deleted;
      stale "current state" sections in the spec and `CLAUDE.md`
      refreshed.
