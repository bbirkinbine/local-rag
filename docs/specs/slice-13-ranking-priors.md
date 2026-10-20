# Slice 13 — Recency, status, and path priors in ranking (opt-in)

Depends on slice 12 (`note_date` / `status` on every chunk). Off by
default: with no `[ranking]` settings, ordering is identical to today.

## Goal

When hybrid hits are near-ties, prefer current notes over stale or retired
ones, without letting a fresh but weakly related note beat a strongly
related old one. The design rule is the existing lexical boost's: a bounded
tie-breaker that can't bridge a real semantic gap.

## Success criteria

### Config

A new optional top-level table, plus one optional per-source key:

```toml
[ranking]
recency_weight = 0.0            # default: no recency boost
recency_half_life_days = 365    # default
status_adjust = {}              # default; e.g. { archived = -0.03 }

[[sources]]
# …
path_adjust = {}                # default; e.g. { "old-import/**" = -0.03 }
```

- **Recency:** `recency_weight * 0.5 ** (age_days / half_life)`, where
  `age_days` is today minus `note_date`, clamped at 0 so a future date gets
  at most the full weight. No `note_date` gives no boost.
- **Status:** `status_adjust` maps a status value to an adjustment,
  matched case-insensitively against the hit's `status`. No status, or an
  unlisted one, gives 0.
- **Path:** `path_adjust` maps a glob, relative to that source's root and
  using the same syntax as `ignore`, to an adjustment. The first matching
  glob wins, in file order. It's per-source because globs are relative to
  a source root (open question 2).
- **Cap.** Config validation rejects settings where two hits'
  combined adjustments could differ by more than **0.10**: the largest
  possible positive total (`recency_weight` plus the largest positive status
  and path values) minus the most negative total (the smallest negative
  status and path values) must be ≤ 0.10. That keeps the priors' total
  swing below the 0.15 lexical-boost ceiling. The error names the offending
  total and the keys behind it.
- Weights must be finite numbers. `recency_weight` must be ≥ 0, and
  `recency_half_life_days` must be > 0. Bad types or values raise
  `ConfigError`.

### Ranking

- New module `local_rag.ranking`:

  ```python
  @dataclass(frozen=True)
  class Priors:
      ...
      @classmethod
      def from_config(cls, config: Config) -> Priors: ...
      def adjustments(self, source_name: str, chunk: Chunk, today: date) -> dict[str, float]: ...
      @property
      def active(self) -> bool: ...   # False when every setting is neutral
  ```

  `adjustments` returns only the non-zero entries, keyed `"recency"`,
  `"status"`, `"path"`.
- `Store.search_hybrid(..., priors: Priors | None = None, today: date | None = None)`.
  The final ranking value is `cosine + bm25_boost + sum(adjustments)`. It
  applies only when ordering the fused candidate pool; candidate gathering
  and `search_vector` don't change. RRF still breaks exact ties.
- `SearchHit` gains `boosts: dict[str, float]`, defaulting to empty.
  `score` includes the adjustments; `cosine` and `bm25` stay raw.
- "Today" is injectable: tests pass it in, and production computes it per
  query (UTC date), so a long-lived MCP server doesn't freeze its clock.

### Surfaces

- **MCP:** `search` passes the configured priors. `SearchResult` gains
  `boosts: dict[str, float]` (additive). The docstring explains that `score`
  may include small recency/status/path adjustments and that `boosts` shows
  them; it tells the caller to judge strength with `cosine`.
- **CLI:** `search` passes the priors and appends each non-zero adjustment
  to the hit header (`recency=+0.021 status=-0.030`).
- **Eval:** `evaluate` takes `priors`. `local-rag eval` uses the configured
  priors, and `local-rag eval --no-priors` turns them off, so one config
  gives the before/after numbers for the eval gate.

### Eval gate

Before enabling anything for the vault, add 2–3 golden queries where an
older and a newer note disagree on a topic and the newer note is expected
(in `eval/golden.local.toml`, never committed). Report `eval --no-priors`
and `eval` with the recommended settings on. The settings ship enabled in
your config only if recall@5 and MRR on the existing queries don't drop and
the new queries improve. The repo default stays neutral either way.

## Non-goals

- Excluding archived notes outright, or filter parameters on `search`.
- Learned or auto-tuned weights.
- Recency from file modification time.
- Priors on `search_vector` (vector-only) results.
- Shipping non-zero defaults. `docs/configuration.md` suggests starting
  values (`recency_weight = 0.03`, `status_adjust = { archived = -0.03 }`)
  for the user to try, but the shipped defaults stay neutral.

## Files

Over the 5-file threshold — needs approval as a set.

- `src/local_rag/ranking.py` (new) — `Priors`, cap validation helper.
- `src/local_rag/config.py` — `[ranking]` table, per-source `path_adjust`.
- `src/local_rag/models.py` — `SearchHit.boosts`.
- `src/local_rag/store.py` — `search_hybrid` applies priors.
- `src/local_rag/evals.py` — pass priors through.
- `src/local_rag/cli.py` — pass priors to `search`/`eval`, `--no-priors`,
  header. Already 375 lines; see open question 1.
- `src/local_rag/mcp_server.py`, `src/local_rag/mcp_types.py` — priors,
  `boosts`, docstring.
- Docs: `docs/configuration.md`, `config.example.toml`, `docs/cli.md`,
  `TODO.md`.
- Tests: `tests/test_ranking.py` (new), `tests/test_config.py`,
  `tests/test_store.py`, `tests/test_evals.py`, `tests/test_cli.py`,
  `tests/test_mcp_server.py`, `tests/test_config_example.py`.

## Tests

Priors (`test_ranking.py`):
- Neutral config: `active` is false and `adjustments` is `{}`.
- Recency is half the weight at one half-life of age.
- No `note_date` gives no recency entry.
- A future date is clamped to the full weight.
- Status matches case-insensitively.
- An unlisted status gives no entry.
- Path: the first matching glob wins.
- Path globs are relative to the hit's source root.

Config:
- Defaults when `[ranking]` and `path_adjust` are absent.
- Settings within the cap load.
- Settings over the cap are rejected (positive side, negative side, and
  mixed).
- Bad types or values are rejected (negative weight, zero half-life, a
  non-numeric adjustment, a bool).

Store (temp-dir DB):
- Regression guard: neutral priors give exactly today's ordering and scores.
- A newer note wins a constructed near-tie.
- An older note with a clearly higher cosine still wins.
- `boosts` holds the non-zero adjustments, and `score` = cosine + lexical
  boost + their sum.
- `search_vector` is unaffected.

Eval / CLI / MCP:
- `evaluate` with priors changes the order in a constructed near-tie.
- `eval --no-priors` matches a run with neutral config.
- CLI header shows non-zero adjustments.
- MCP `SearchResult.boosts` matches the hit.
- With neutral config, `boosts` is `{}`.

## Open questions

1. **`cli.py` size.** It's at 375 lines and grows again here. Recommended:
   first commit of this slice moves the `_print_*` helpers into a new
   `src/local_rag/cli_output.py` (pure move, no behavior change), which
   adds one file to the set.
2. **Per-source `path_adjust` vs. global.** Recommended: per-source, since
   globs are relative to a source root and folder layouts differ by source.
   A global table would need source-qualified globs.
3. **Cap measured as swing (max positive minus max negative) rather than
   per-hit absolute value.** Recommended: swing. A per-hit cap of 0.10
   still lets one hit gain +0.10 while another loses 0.10, a 0.20 gap that
   exceeds the lexical ceiling.

## Verification

```
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src/
uv run local-rag eval --no-priors      # baseline
uv run local-rag eval                  # with priors configured
```
