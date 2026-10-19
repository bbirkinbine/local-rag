# Slice 12 — Frontmatter metadata on chunks

Carry note dates, status, and tags from YAML frontmatter into the index and
out through search results, and add a `kind` column reserved for derived
chunk types. This is the one schema change planned for the current batch of
work, so later slices that emit new chunk kinds or rank on metadata reuse
these columns instead of forcing another reindex.

## Goal

`chunk_markdown` strips frontmatter today (`_frontmatter_end`), so none of
it reaches LanceDB. A caller that gets two hits that disagree can't tell
which note is newer or whether one is retired. After this slice every hit
carries `note_date`, `status`, `tags`, and `kind`, and the tool docs tell
the caller how to use them. Ranking is unchanged.

## Success criteria

### Frontmatter parsing (`local_rag.frontmatter`, new)

```python
@dataclass(frozen=True)
class FrontmatterFields:
    date_fields: tuple[str, ...] = ("updated", "created")
    status_field: str = "status"
    tags_field: str = "tags"


@dataclass(frozen=True)
class NoteMeta:
    note_date: datetime.date | None = None
    status: str | None = None
    tags: tuple[str, ...] = ()


def parse_frontmatter(block: str, fields: FrontmatterFields) -> NoteMeta: ...
```

- Stdlib only (no PyYAML — see decision 1).
- Understands, per key: flat `key: value` scalars, quoted scalars (`'…'`,
  `"…"`), inline lists (`[a, b]`), and block lists (`- a` lines under the
  key). Comments (`# …` after whitespace) and blank lines are ignored.
- Anything else for a key — nested maps, anchors/aliases, multi-line
  (`|`, `>`) strings — yields "absent" for that key only; other keys still
  parse.
- `note_date`: the first configured date field, in order, whose value
  starts with a valid `YYYY-MM-DD` (so `2026-10-19`, `2026-10-19T09:30`,
  and `"2026-10-19 09:30"` all give `2026-10-19`). Invalid dates
  (`2026-13-40`) and non-date values are skipped and the next field is
  tried.
- `status`: a scalar string, whitespace-stripped, case preserved. A list or
  empty value gives `None`.
- `tags`: a list of strings. A scalar is split on commas and whitespace
  (Obsidian tags can't contain spaces). A leading `#` is stripped,
  duplicates are dropped keeping first-seen order, empties are dropped.
- Never raises on content. Anything unparseable gives an empty `NoteMeta`
  plus one `structlog` warning naming the file. A file is never skipped
  because of its frontmatter.

### Chunks carry metadata

- `Chunk` gains four fields with defaults, so every existing constructor
  call keeps working:
  `note_date: date | None = None`, `status: str | None = None`,
  `tags: tuple[str, ...] = ()`, `kind: str = "text"`.
- `chunk_markdown` and `chunk_file` take an optional
  `fields: FrontmatterFields` (default: the generic defaults). Every chunk
  of a markdown file carries that file's `NoteMeta`; `kind = "text"`.
- `chunk_code` output carries nulls, `()`, and `kind = "text"`.
- Frontmatter stays out of chunk text and embeddings. Char offsets and the
  `text[c.char_start:c.char_end] == c.text` invariant are unchanged.

### Config

Per-source keys, all optional, generic defaults:

```toml
[[sources]]
name = "notes"
path = "~/notes"
type = "markdown"
date_fields = ["updated", "created"]   # default
status_field = "status"                # default
tags_field = "tags"                    # default
```

- Validated like the existing keys (list of non-empty strings / non-empty
  string; `ConfigError` with the source name otherwise).
- Applies to every file the dispatcher chunks as markdown (`.md`), whatever
  the source `type` — a repo's docs can carry frontmatter too.
- Changing these keys doesn't change any file hash, so it takes
  `index --force` to apply. The docs say so.

### Schema and migration

- New columns: `note_date` (`date32`, nullable), `status` (`string`,
  nullable), `tags` (`list<string>`, non-null, may be empty), `kind`
  (`string`, non-null).
- A table is **stale** when its schema lacks any expected column.
  `Store.stale_tables(names) -> list[str]` reports them (pure schema read,
  no row scan).
- `local-rag index` checks every targeted source before indexing anything.
  If any table is stale and `--force` wasn't given: print a message naming
  the stale sources and `local-rag index --force`, record that message as
  the run error for those sources (so `index_status` shows it), exit **4**,
  and touch nothing.
- `local-rag index --force` drops and recreates each stale table, then
  indexes it in full. Tables already current keep today's `--force`
  behavior (re-embed in place, history retained). The dropped table's
  run-log history is cleared so retention never reasons about versions
  that no longer exist.
- `search` (CLI and MCP) against a stale table keeps working: new fields
  come back `null` / `[]` / `"text"`. Mixed stale + current sources in one
  query work.

### Search results

- `SearchHit` is unchanged (metadata rides on `hit.chunk`).
- MCP `SearchResult` gains `note_date: str | None` (ISO `YYYY-MM-DD`),
  `status: str | None`, `tags: list[str]`, `kind: str` — additive.
- CLI `search` output appends `date=YYYY-MM-DD` and `status=<value>` to a
  hit's header line when present. Tags are not printed.
- `search` tool docstring and server instructions: weigh `note_date` when
  hits disagree (newer usually wins); `status` can mark a retired note;
  `kind` is `"text"` today.

## Non-goals

- Any ranking change (recency/status priors are a later, opt-in slice).
- Filter parameters on `search` (tags, status, date range).
- Inline `#tags` in the note body.
- File modification time as a date (sync tools overwrite it).
- Frontmatter on non-`.md` files (`.mdx` is chunked as code today; unchanged).
- Showing schema staleness in `list` / `index_status` beyond the run error
  recorded by a refused `index`.
- Auto-migrating without `--force`.

## Files

Over the 5-file threshold — approved as a set.

- `src/local_rag/frontmatter.py` (new) — `FrontmatterFields`, `NoteMeta`,
  `parse_frontmatter`.
- `src/local_rag/models.py` — four `Chunk` fields.
- `src/local_rag/chunkers.py` — call the parser, attach metadata
  (`chunkers.py` is at 296 lines; the parser lives in its own module, and
  the oversize-splitting helpers move to `src/local_rag/chunk_split.py`
  to keep it under 300).
- `src/local_rag/config.py` — three per-source keys.
- `src/local_rag/store.py` — schema, row mapping, `stale_tables`,
  drop-and-recreate. Already over 300 lines; the schema definition and
  staleness check move to a new `src/local_rag/schema.py` so this slice
  doesn't grow it.
- `src/local_rag/indexer.py` — pass the source's fields to `chunk_file`;
  recreate stale tables on `force`.
- `src/local_rag/cli.py` — pre-index staleness check (exit 4), hit header.
- `src/local_rag/mcp_server.py` — result fields, docstring, instructions.
  At 300 lines; the result `TypedDict`s move to `src/local_rag/mcp_types.py`.
- Docs: `docs/configuration.md`, `config.example.toml`, `docs/cli.md`
  (exit 4, header fields), `TODO.md`.
- Tests: `tests/test_frontmatter.py` (new), `tests/test_chunkers.py`,
  `tests/test_config.py`, `tests/test_store.py`, `tests/test_indexer.py`,
  `tests/test_cli.py`, `tests/test_mcp_server.py`.

## Tests

Parser (`test_frontmatter.py`):
- `updated` beats `created`.
- Falls back to `created` when `updated` is missing.
- Skips an unparseable `updated` and uses `created`.
- Datetime-style values give their date part.
- Quoted scalar values are unquoted.
- Inline tag list parses.
- Block tag list parses.
- Scalar tags split on commas/whitespace; leading `#` stripped; dupes dropped.
- List-valued `status` gives `None`.
- A nested map under one key leaves other keys parsed.
- Garbage frontmatter gives empty `NoteMeta` without raising.
- Custom field names are honored.

Chunkers:
- Every chunk of a markdown file carries the file's metadata.
- A markdown file with no frontmatter gives nulls / `()`.
- Code chunks carry no metadata.
- Chunk text and offsets are identical with and without metadata parsing
  (regression guard on the round-trip invariant).

Config:
- Defaults applied when keys are absent.
- Custom keys parsed.
- Invalid types rejected with the source name in the message.

Store (temp-dir DB, no mocks):
- Metadata round-trips through upsert → `search_hybrid` and `search_vector`.
- `stale_tables` flags a table built with the pre-slice schema.
- `stale_tables` passes a freshly created table.
- Search on a stale table returns hits with default metadata.

Indexer / CLI:
- `index` on a stale table exits 4, names `index --force`, writes nothing.
- The refused run is recorded as the source's run error.
- `index --force` on a stale table recreates it with the new schema.
- `search` output shows `date=` and `status=` when present.

MCP:
- `SearchResult` carries the four new fields with the hit's values.
- A hit from a stale table gives `null` / `[]` / `"text"`.

The pre-slice schema fixture is built in the test by creating a LanceDB
table with today's column list directly — no checked-in binary fixture.

## Decisions

1. **Stdlib parser, no PyYAML.** The needed subset is small, and PyYAML
   would still need normalization (it turns bare dates into `date` objects
   and `on`/`no` into booleans).
2. **Exit code 4** for "needs `--force`" (2 = usage/config, 3 = embedder
   unreachable).
3. **Deployment.** The scheduled indexer runs `index` without `--force`,
   so once this merges it fails (exit 4, visible in `index_status`) until
   `local-rag index --force` runs once. MCP servers keep serving from the
   old tables meanwhile. Run `index --force` right after merging, then
   restart MCP clients so they load the new result shape.

## Verification

```
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src/
uv run local-rag index            # on a pre-slice DB: exit 4, names --force
uv run local-rag index --force
uv run local-rag search "some note topic"   # date=/status= on vault hits
uv run local-rag eval             # recall@5 / MRR unchanged (no ranking change)
```
