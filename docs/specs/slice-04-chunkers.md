# Slice 04 — chunkers

Fourth implementation slice. Turn raw file text into the `Chunk` rows the store
already speaks. Two strategies — markdown (header-aware) and code (line-window) —
with a thin dispatcher that picks one based on file extension.

## Goal

Implement `local_rag.chunkers` so the indexer (slice 5) can do
`text → chunks → embed → store.upsert` without owning any chunking logic itself.

## Success criteria

### Module (`local_rag.chunkers`)

Three public functions plus the suffix dispatch:

- `chunk_markdown(text: str, *, source_path: str, file_hash: str) -> list[Chunk]`
  — header-aware. Strips YAML frontmatter (`---\n…\n---\n` at the very start of
  the file) before splitting. Splits on lines matching `^#{1,6} `; each chunk is
  the body under one heading, with `heading_path` = `/H1/H2/…` reflecting the
  current ATX header stack. Pre-heading content emits a chunk with
  `heading_path = ""` only if non-empty.
- `chunk_code(text: str, *, source_path: str, file_hash: str, window_lines: int = 60, overlap_lines: int = 10) -> list[Chunk]`
  — fixed-size line windows with overlap. `heading_path = ""`. Last window
  shrinks to fit; we never re-emit the same final lines as a tiny tail chunk.
- `chunk_file(text: str, *, source_path: str, file_hash: str) -> list[Chunk]`
  — dispatcher. `.md` → `chunk_markdown`; anything else → `chunk_code`.

`Chunk.vector` is always `[]` from the chunker — the indexer fills it after
embedding, via `dataclasses.replace(chunk, vector=v)`.

### Shared chunk shape

Both chunkers populate:

- `source_path`, `file_hash` — passed through verbatim.
- `chunk_index` — 0-based, monotonically increasing per file.
- `char_start`, `char_end` — half-open offsets into the **original** input
  string (not the frontmatter-stripped one). `text[char_start:char_end]` must
  equal `Chunk.text` exactly.
- `heading_path` — see above.
- `text` — the chunk content. For markdown: includes the heading line that
  introduced it (so the embedder sees "## Foo\n\nbody"). For code: raw window.
- `vector` — `[]`.

### Behavioral rules

- **Empty input** (`""` or whitespace-only) → `[]`. No empty chunks.
- **No headings in markdown** → fall back to a single chunk with
  `heading_path = ""` and the whole document body as `text`.
- **YAML frontmatter** is detected only if the file *starts* with `---` on its
  own line, followed by any content, then a closing `---` on its own line. If
  the closing fence is missing, treat the input as non-frontmatter and chunk
  normally.
- **Heading stack discipline:** `#` resets the stack to depth 1; `###` after a
  `#` (skipping `##`) still nests at depth 2 in `heading_path` — we don't try to
  guess intent. So `# A` then `### B` → `/A/B`.
- **Char offsets honor frontmatter:** if frontmatter is stripped, the first
  post-frontmatter character's `char_start` is the byte offset *after* the
  closing `---\n`, not 0.
- **Line windows are byte-faithful:** `chunk_code` slices on `\n` boundaries.
  `char_start` of window N = `char_end` of window N-1's first non-overlapping
  line. Each window's `text` is `original[char_start:char_end]`.
- **No file I/O.** Chunkers take strings, not paths. Suffix dispatch reads
  `source_path` only to pick md vs code.

## Non-goals

- No extension allowlist enforcement — that lives in the indexer (slice 5),
  which decides which files to read at all.
- No 1 MB file-size skip — also indexer.
- No `.gitignore` walking — also indexer.
- No embedding. `Chunk.vector` is `[]` from the chunker. The indexer enriches.
- No tree-sitter / function-level chunking. Line-window is the v1 rule.
- No `.rst` / `.txt` special-casing — they go through the code chunker. RST
  has headers but they're underline-based; not worth a separate parser in v1.
- No language-aware code splitting (no respect for `def`/`class` boundaries).

## Files

- `src/local_rag/chunkers.py` (new)
- `tests/test_chunkers.py` (new)

## Tests

Markdown:
- Empty / whitespace-only input → `[]`.
- No headings → one chunk, `heading_path=""`, full text preserved.
- Single heading → one chunk, `heading_path="/Title"`, text includes the
  heading line.
- Nested headings build the path: `# A` → `## B` → `### C` → `/A/B/C`.
- Sibling heading at same level replaces the last segment, not appends:
  `# A` → `## B` → `## C` → second chunk path is `/A/C`.
- Pop on shallower heading: `# A` → `## B` → `# C` → second-chunk path under
  `C` is just `/C`.
- YAML frontmatter is stripped; `char_start` of the first chunk is past the
  closing `---`.
- Frontmatter with no closing fence is not stripped — treat as content.
- `chunk.text == original[chunk.char_start:chunk.char_end]` holds for every
  chunk (round-trip property).
- `chunk_index` is 0, 1, 2, … in order.
- Pre-heading body (content before any `#`) becomes its own chunk with
  `heading_path=""`.
- Skipped levels: `# A` then `### B` → second-chunk path is `/A/B` (no synth).

Code:
- Empty input → `[]`.
- File < window_lines → single chunk covering everything.
- File at exactly window_lines → single chunk.
- File at window_lines + 1 → two chunks; second starts at line
  `window_lines - overlap_lines` of the original.
- Round-trip: every chunk's `text` equals `original[char_start:char_end]`.
- `chunk_index` increments from 0.
- Overlap behaves: consecutive chunks' line ranges overlap by `overlap_lines`.
- Custom window/overlap parameters are honored.

Dispatcher:
- `.md` extension → markdown chunker (verified by presence of `heading_path`).
- `.py` / `.txt` / `.rst` / no extension → code chunker.
- Case-insensitive extension match (`.MD` works too).

Shared:
- Every chunk has `vector == []`.
- `file_hash` and `source_path` pass through unchanged.

## Verification

```
uv run pytest tests/         # 63 prior + ~25 new
uv run ruff check src/ tests/
uv run mypy src/             # strict
```
