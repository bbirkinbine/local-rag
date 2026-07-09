# TODO

## Open — ranking quality

The 2026-07-09 ranking-quality push (eval harness → chunk cap → score
transparency → cosine re-scoring, all landed the same day) took the
golden-query set from **recall@5 = 0.000 / MRR = 0.000** to
**recall@5 = 0.667 / MRR = 0.400**. Remaining open items:

- [ ] **The remaining golden-query miss is a near-miss**: the expected
      note is outranked by eight sibling notes on the same topic, all
      tightly clustered at cosine 0.65–0.69 — arguably correct retrieval
      against a strict golden entry. Revisit only if real usage shows the
      "specific note among close siblings" case matters; candidate levers
      are heading-path/title term boosts or BM25-weighted blending.
- [ ] **Grow the golden set** as new real queries succeed or fail in
      Cowork/Claude Code sessions (gitignored `eval/golden.local.toml`);
      three queries is too few to trust the metrics' stability. An
      independent audit (2026-07-09) confirmed the reported numbers but
      flagged their k-sensitivity: recall@3 = 0.333, recall@10 = 1.000.
- [ ] **Down-weight list-heavy chunks** (>70% of lines are bullets) —
      deferred; the chunk cap + cosine ordering already demoted the
      keyword-dense list notes that motivated it.
- [ ] **Frontmatter filters & boosts** (tags, folder path, mild mtime
      recency) — only if still needed after real usage.

Constraints unchanged: keep `search(query, sources, k)` backward
compatible, keep indexing incremental, and give any index-format change a
migration path (`index --force` is the reindex path for chunker changes).

## Done — ranking quality & tool adoption (2026-07-09)

- [x] **Eval harness** — `local-rag eval` runs a golden-query set against
      the live index and reports file-level recall@5 / MRR (expected paths
      suffix-matched on path-component boundaries). Template checked in at
      `eval/golden.example.toml`; real data stays in gitignored
      `eval/golden.local.toml`. Baseline measured: 0.000 / 0.000.
- [x] **Cap markdown chunk size** — sections split at ~1,200 chars on
      paragraph boundaries with 150-char overlap (`chunkers.py`). Added
      `index --force` as the migration path; full vault re-embedded.
      Cap alone did not move recall@5 off 0.000 — the fused ranking was
      the binding constraint (see re-scoring below).
- [x] **Expose real scores** — hits now carry `cosine` (computed for
      every hybrid hit) and `bm25` (when a lexical match exists) alongside
      the RRF `score`, in the CLI (`cos=`/`bm25=`), MCP results, and
      `SearchHit`.
- [x] **Cosine re-scoring** — final ordering of the fused candidate pool
      is now raw cosine (RRF breaks ties). Diagnosis via the new score
      fields: RRF rank fusion let mediocre-cosine keyword matches
      (including this repo's own docs, which meta-match search vocabulary)
      beat 0.66+-cosine notes ranked 46th/81st in fused order. This change
      alone took recall@5 from 0.000 to 0.667.
- [x] **Sharpen MCP instructions + `search` description** — states when
      `search` beats client grep and what each score means.
- [x] **`context_chunks` parameter on `search`** (0–5) — stitches
      neighboring chunks onto each hit via char offsets (overlap
      deduplicated); matters for Cowork now that chunks are capped small.
- [x] **Lexical blend in final ordering** — the ranking value is now
      `cosine + 0.15 * bm25/(bm25+10)`: a saturating keyword boost that
      wins near-ties (identifier and note-title lookups) but can't bridge
      a real semantic gap. Motivated by two exact-keyword golden queries
      added first as guards: one (a code identifier) was a complete miss
      under cosine-only ordering despite being the true BM25 #1. Eval on
      the 5-query set: recall@5 0.600 -> 0.800, MRR 0.340 -> 0.500 (the
      original 3 queries: recall unchanged, MRR 0.400 -> 0.500). Hybrid
      is also no longer structurally identical to vector-only search.
- [x] **Fix nondeterministic FTS results** (found by an independent audit
      of the scoring change). LanceDB 0.30's native FTS misbehaves against
      unmerged `merge_insert` deltas: `limit(n)` returns an arbitrary
      unsorted sample (zero overlap with the true top-n on the live
      table), scores use stale corpus statistics, and some corpora hide
      matching rows entirely. Fixed twice over: `Store.optimize()` merges
      deltas at the end of every indexing run, and `Store._fts_top()`
      recovers exact score ordering at query time (fetch all match
      scores light, sort, refetch top rows). Hybrid k=5 latency: ~100 ms.

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
