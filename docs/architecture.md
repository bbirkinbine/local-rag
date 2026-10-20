# Architecture

How local-rag is put together, why each part was chosen, and exactly what a
model sees when it searches. For setup, start with the [README](../README.md);
for the original design record, see the [spec](specs/local-rag.md) and the
per-slice specs beside it.

## The parts at a glance

```text
  sources               indexing (local-rag index)                  store                    surfaces
 ─────────────   ──────────────────────────────────────────    ─────────────────     ──────────────────────
  notes vault ─┐   walk ─► hash ─► chunk ─► embed ─► upsert  ─►  LanceDB             ┌─► MCP stdio server ─► Claude Code,
  repo A ──────┼─► (allow- (SHA-   (md      (Ollama  (+ merge,   one table per   ────┤     search / list_sources   Cowork, any
  repo B ──────┘    list)   256)   headings, bge-m3)  FTS        source, + run log   │     / index_status          MCP client
                                   code               rebuild,   + freshness file    └─► CLI ─► humans, scripts,
                                   windows)           prune)                              CLI-first agents
```

| Part | Module | Job |
| --- | --- | --- |
| Config | `config.py` | Parse and validate `config.toml`: sources, embedding, store and frontmatter settings. |
| Walker | `indexer.py` | List the files of one source: extension allowlist, 1 MB cap, `ignore` globs, optional `.gitignore`. |
| Chunkers | `chunkers.py`, `chunk_split.py` | Split markdown at headings and code into line windows; cap chunk size. |
| Frontmatter | `frontmatter.py` | Read a note's date, status and tags from its YAML frontmatter. |
| Embedder | `embedder.py` | Batch text through Ollama's `/api/embed`; health-check the model. |
| Store | `store.py`, `schema.py` | LanceDB tables, hybrid search, index maintenance, schema checks. |
| Indexer | `indexer.py` | Orchestrate walk → chunk → embed → store for one source, incrementally. |
| Freshness | `run_status.py` | Record every indexing run per source for `index_status`. |
| Eval | `evals.py` | Score retrieval against a golden-query set (recall@k, MRR). |
| MCP server | `mcp_server.py`, `mcp_types.py` | Expose search to MCP clients over stdio. |
| CLI | `cli.py` | `index`, `search`, `list`, `eval`, `mcp`. |

## Why these choices

**Local only.** Embeddings, vectors and queries never leave the machine:
no cloud APIs, no keys, no quotas. Every other choice is made inside that
constraint.

**LanceDB for storage.** An embedded database (no server to run) that keeps
one table per source cleanly and reads/writes Arrow natively. It ships a
full-text index, so keyword search needs no second system. sqlite-vec was
the runner-up; it handles one or two corpora well but separates less cleanly
past that. No vector index is built: a brute-force scan is fast enough under
~100k chunks per table, and the trigger to revisit is query latency over
500 ms.

**Ollama with `bge-m3` for embeddings.** Ollama serves models locally over
HTTP with nothing to compile. `bge-m3` is strong on retrieval benchmarks, has
an 8,192-token window, handles many languages, and emits unit-length
vectors, so cosine similarity is the natural metric. It was picked over the
smaller `nomic-embed-text` because recall mattered more than memory on the
target hardware. The batch endpoint (`/api/embed`, 64 texts per request) is
several times faster than one call per chunk on a first full index.

**Hybrid search, ordered by cosine.** Pure vector search misses exact-term
lookups (identifiers, note titles, codenames), and pure keyword search
misses notes that share meaning but not words. Both modalities gather
candidates; see [Search and ranking](#search-and-ranking) for how they are
ordered and why.

**Header-aware markdown chunks, line-window code chunks.** Splitting notes at
headings keeps each chunk on one topic and gives every hit a `heading_path`
(`/Title/Section/Subsection`). Sections are capped at ~1,200 characters,
split at paragraph breaks with 150 characters of overlap, because unbounded
sections diluted embeddings and let keyword-dense notes dominate. Code uses
60-line windows with 10 lines of overlap; function-level (tree-sitter)
chunking is on hold until there's evidence the tool is used for in-repo code,
where an agent's own grep usually wins.

**Frontmatter as metadata, not text.** Frontmatter is kept out of chunk text
and embeddings because it is metadata noise that skews vectors. Its date,
status and tags ride on every chunk of the note instead, so a caller can
tell which of two disagreeing notes is newer or retired. The parser is
stdlib-only and reads just the YAML subset notes use; a note is never skipped
over bad frontmatter.

**Incremental indexing by content hash.** Each file's SHA-256 is stored with
its chunks; an unchanged file is never re-read or re-embedded, so a run where
little changed finishes in seconds and frequent scheduled runs are cheap.
There is no file watcher: indexing is an explicit CLI run, usually on a
schedule ([deployment](deployment.md)).

**Opt-in sources.** Nothing is indexed unless a `[[sources]]` block names it,
so cloned third-party repos stay invisible by default.

**stdio MCP.** Claude Code and Cowork both spawn stdio servers themselves, so
there is no daemon, port or auth to manage. An HTTP transport was built and
removed once it had no consumer.

**The calling model is the reranker.** No cross-encoder reranks hits
locally: a frontier model reading the top-k and choosing what's relevant does
that job better. local-rag's work is to get the right chunks into the top-k
and expose honest scores.

**Few dependencies.** No `langchain` or `llama-index`: chunking is a few
hundred lines and the frameworks would dwarf the project. Config parsing uses
stdlib `tomllib`.

## Indexing pipeline

`local-rag index` runs this per source:

1. **Schema check.** If any targeted table was built by an older version
   that lacks current columns, `index` refuses (exit 4) and touches nothing
   until `index --force` rebuilds it. See [CLI](cli.md#schema-upgrades).
2. **Health check.** Ollama must answer and return unit-length vectors, or
   the run stops (exit 3). Both refusals are recorded for `index_status`.
3. **Walk.** Files on the extension allowlist (`.md`, `.txt`, `.rst`, `.py`,
   `.js`, `.ts`, `.go`, `.rs`, `.toml`, `.yaml`, `.json` and a few more),
   under 1 MB, not matched by `ignore` or (optionally) `.gitignore`.
4. **Hash.** Unchanged files are skipped. Files gone from disk have their
   chunks deleted.
5. **Chunk.** `.md` files get the markdown chunker and frontmatter metadata;
   everything else gets line windows.
6. **Embed.** Batches go to Ollama. A chunk over 30,000 characters fails
   loudly rather than being silently truncated.
7. **Upsert.** A changed file's old chunks are replaced.
8. **Maintain.** If anything changed, LanceDB deltas are merged, the
   full-text index is rebuilt to a single partition, and table history is
   pruned to the last `keep_runs` runs ([configuration](configuration.md#keep_runs)).
9. **Record.** The run's time, changes, model and failures go to the
   freshness file.

A failure on one file (unreadable, embed error) is recorded and the run
continues.

## Storage

Everything lives under `db_path`:

- **One LanceDB table per source.** Columns: `id`, `source_path`,
  `file_hash`, `chunk_index`, `char_start`, `char_end`, `heading_path`,
  `text`, `vector` (1024 floats), `note_date`, `status`, `tags`, `kind`.
  `char_start`/`char_end` are offsets into the original file, so
  `file[char_start:char_end] == text`. `kind` is `"text"` for every chunk
  today; it is reserved for derived chunk types.
- **`.run_log.json`** records which indexing runs changed each table; it
  decides how much table history is pruned.
- **`.index_status.json`** records every run per source (last run, last
  change, model, startup error, failed files) for `index_status`.

The store is fully derived from the sources. `local-rag index --force`
rebuilds it from scratch at any time.

## Search and ranking

Hybrid search, used by the MCP `search` tool, `local-rag search` and
`local-rag eval`:

1. **Gather.** For each searched source, take the top `max(4k, 50)` chunks by
   vector similarity and the top `max(4k, 50)` by BM25 keyword score.
2. **Score.** Compute exact cosine similarity for every candidate.
3. **Order.** Rank by `cosine + 0.15 × bm25 / (bm25 + 10)`. The keyword boost
   saturates below 0.15, so a strong exact match wins a near-tie (an
   identifier or note title) but can't lift a weakly related chunk past a
   strongly related one. Reciprocal Rank Fusion of the two candidate lists
   breaks exact ties.
4. **Return** the top `k`.

This ordering came out of the eval harness. Pure RRF ordering let
keyword-dense chunks with weak meaning outrank strongly similar notes, and
pure cosine ordering made exact-keyword lookups unreachable. The blend fixed
both on the golden set.

LanceDB's native full-text search returns unordered results when the table
has unmerged writes, so the store merges after every indexing run and
recovers exact BM25 order at query time. Typical hybrid latency at k=5 is
about 100 ms.

Ranking ignores metadata today: `note_date` and `status` are returned for the
caller to weigh.

## What models can use

### MCP tools

The server registers three tools, plus server instructions that tell the
model when `search` beats its built-in grep: when the match is conceptual
(no shared keywords), or the content is outside the current workspace (the
vault from a coding session, repos from a notes session).

**`search(query, sources=None, k=10, context_chunks=0)`**: hybrid search.

| Parameter | Meaning |
| --- | --- |
| `query` | Free text. Empty or whitespace returns `[]`. |
| `sources` | Source names to search (see `list_sources`). Omitted searches every configured source; unknown names are ignored rather than raising; `[]` searches nothing. |
| `k` | Hits returned; clamped to 1–100. |
| `context_chunks` | 0–5 neighboring chunks stitched onto each side of a hit, for when the chunk alone is too little context and the model can't open the file. |

Each hit:

| Field | Meaning |
| --- | --- |
| `score` | The ranking value (cosine plus the bounded keyword boost). |
| `cosine` | Semantic similarity, the signal to judge hit strength by; a sharp drop-off marks the end of the relevant results. |
| `bm25` | Raw keyword score; `null` when the chunk had no keyword match. |
| `source_name`, `source_path` | Which source and which file. |
| `heading_path` | `/Title/Section` path of the chunk; empty for code. |
| `char_start`, `char_end` | Offsets of the returned text in the file. |
| `text` | The chunk, or the stitched span with `context_chunks`. |
| `note_date` | ISO date from frontmatter (`updated`, else `created`, by default), or `null`. When hits disagree, the newer note usually wins. |
| `status` | Frontmatter status (e.g. a retired note's `archived`), or `null`. |
| `tags` | Frontmatter tags; `[]` if none. |
| `kind` | `"text"`. |

**`list_sources()`**: every indexed table with its chunk count.

**`index_status()`**: per source (indexed or configured), its chunk count,
`last_run` and `last_run_age_seconds`, `last_change`, `last_error` (a
startup failure such as Ollama being down, or a refused schema upgrade), the
embedding model, and failed files grouped by error. The tool description
tells the model to check this before concluding that a missing note doesn't
exist: a stale `last_run` means it may not be indexed yet.

The tool descriptions live in `src/local_rag/mcp_server.py`; they decide
whether a model reaches for local-rag at all, so they state when to use each
tool and how to read the scores.

### CLI

The CLI exposes the same search for humans, scripts and CLI-first agent
harnesses that don't speak MCP. `local-rag search` prints the same scores,
plus `date=` and `status=` when present. Machine-readable `--json` output is
planned (see [TODO](../TODO.md)). Reference: [cli.md](cli.md).

## Measuring retrieval

`local-rag eval` runs a golden-query file against the live index and reports
file-level recall@k and MRR. Negative queries assert that nothing relevant
exists, which catches score inflation. Real queries name personal notes, so
they live in the gitignored `eval/golden.local.toml`; `eval/golden.example.toml`
shows the format. Every ranking change is judged on this before it ships.

## Deliberately absent

- A vector index (until latency calls for one).
- A local reranker (the calling model reranks).
- A filesystem watcher (scheduled indexing instead).
- Cloud models or remote MCP (privacy).
- Ranking by note date or status; it may come later as opt-in, judged by
  the eval.
