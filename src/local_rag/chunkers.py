"""Chunkers that turn raw file text into ``Chunk`` rows.

Two strategies plus a dispatcher:

- ``chunk_markdown`` — header-aware. Strips leading YAML frontmatter, then
  splits the body on ATX headings (``^#{1,6} ``). Each chunk's ``heading_path``
  reflects the current heading stack (``/H1/H2/…``). Pre-heading content
  becomes a chunk with an empty ``heading_path``. v1 limitations: ATX only
  (no setext underlined headings), and no awareness of fenced code blocks —
  a ``# foo`` line inside ```` ``` ```` is still treated as a heading.
- ``chunk_code`` — fixed-size line windows with overlap. No heading semantics.
- ``chunk_file`` — picks one based on the file extension (``.md`` → markdown,
  anything else → code).

Chunkers do no I/O; they take strings. ``Chunk.vector`` is always ``[]`` from
here — the indexer enriches with embeddings before persisting.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import PurePosixPath

from local_rag.models import Chunk

_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6}) +(.+?)\s*$")
_MARKDOWN_SUFFIXES = frozenset({".md"})

# Soft cap on per-chunk character count. The embedder enforces a hard
# 30,000-char limit (bge-m3's ~8192-token window with headroom); we leave
# 6,000 chars between the two so a chunk that almost-hits this cap doesn't
# blow up at the embedder boundary. Long-line files (transcripts, minified
# code, generated logs) routinely have single "lines" of 30k+ chars; without
# this cap they'd produce one chunk per file and fail.
_MAX_CHUNK_CHARS = 24_000

# Retrieval-quality cap for markdown chunks. Unbounded heading sections make
# keyword-dense notes dominate BM25 and dilute the embedding, so sections are
# split to this size at paragraph boundaries, with overlap so a fact straddling
# a cut still lands whole in one chunk.
_MD_MAX_CHUNK_CHARS = 1_200
_MD_CHUNK_OVERLAP_CHARS = 150


def chunk_markdown(text: str, *, source_path: str, file_hash: str) -> list[Chunk]:
    """Header-aware markdown chunking.

    Splits on ATX headings (``^#{1,6} ``). Each chunk contains the heading line
    that introduces it plus all content up to the next heading. Pre-heading
    body, if any, emits its own chunk with ``heading_path=""``. Sections over
    ``_MD_MAX_CHUNK_CHARS`` are further split at paragraph boundaries with
    ``_MD_CHUNK_OVERLAP_CHARS`` of overlap (retrieval-quality cap).

    Args:
        text: The raw markdown file contents.
        source_path: Carried through to each emitted chunk.
        file_hash: Carried through to each emitted chunk.

    Returns:
        Zero or more ``Chunk`` rows. Returns ``[]`` for empty or
        whitespace-only input. Each chunk's ``char_start``/``char_end`` are
        offsets into the **original** ``text`` (pre-frontmatter-strip), so
        ``text[c.char_start:c.char_end] == c.text`` always holds.
    """
    if not text.strip():
        return []

    fm_end = _frontmatter_end(text)
    body_start = fm_end

    chunks: list[Chunk] = []
    heading_stack: list[tuple[int, str]] = []
    buf: list[str] = []
    buf_start = body_start

    def flush() -> None:
        if not buf:
            return
        chunk_text = "".join(buf)
        if not chunk_text.strip():
            return
        chunks.append(
            Chunk(
                source_path=source_path,
                file_hash=file_hash,
                chunk_index=len(chunks),
                char_start=buf_start,
                char_end=buf_start + len(chunk_text),
                heading_path=_format_path(heading_stack),
                text=chunk_text,
                vector=[],
            )
        )

    cursor = body_start
    for line in text[body_start:].splitlines(keepends=True):
        m = _HEADING_RE.match(line)
        if m:
            flush()
            buf = []
            buf_start = cursor
            level = len(m.group(1))
            title = m.group(2).strip()
            heading_stack = [(lvl, t) for lvl, t in heading_stack if lvl < level]
            heading_stack.append((level, title))
        buf.append(line)
        cursor += len(line)

    flush()
    return _split_oversized(
        chunks,
        max_chars=_MD_MAX_CHUNK_CHARS,
        overlap=_MD_CHUNK_OVERLAP_CHARS,
    )


def chunk_code(
    text: str,
    *,
    source_path: str,
    file_hash: str,
    window_lines: int = 60,
    overlap_lines: int = 10,
) -> list[Chunk]:
    """Fixed-size line-window chunking with overlap.

    Args:
        text: The raw file contents.
        source_path: Carried through to each emitted chunk.
        file_hash: Carried through to each emitted chunk.
        window_lines: Lines per chunk. Must exceed ``overlap_lines``.
        overlap_lines: Lines shared with the previous chunk.

    Returns:
        Zero or more ``Chunk`` rows. Returns ``[]`` for empty input. Each
        chunk's ``heading_path`` is ``""``. Round-trip holds:
        ``text[c.char_start:c.char_end] == c.text``.

    Raises:
        ValueError: if ``window_lines <= overlap_lines`` (would not advance).
    """
    if not text:
        return []
    step = window_lines - overlap_lines
    if step <= 0:
        raise ValueError(
            f"window_lines ({window_lines}) must be greater than overlap_lines ({overlap_lines})"
        )

    lines = text.splitlines(keepends=True)
    n = len(lines)
    if n == 0:
        return []

    prefix: list[int] = [0]
    for line in lines:
        prefix.append(prefix[-1] + len(line))

    chunks: list[Chunk] = []
    start = 0
    while start < n:
        end = min(start + window_lines, n)
        char_start = prefix[start]
        char_end = prefix[end]
        chunks.append(
            Chunk(
                source_path=source_path,
                file_hash=file_hash,
                chunk_index=len(chunks),
                char_start=char_start,
                char_end=char_end,
                heading_path="",
                text=text[char_start:char_end],
                vector=[],
            )
        )
        if end == n:
            break
        start += step

    return _split_oversized(chunks)


def chunk_file(text: str, *, source_path: str, file_hash: str) -> list[Chunk]:
    """Dispatch to the right chunker based on ``source_path``'s extension.

    ``.md`` (case-insensitive) → :func:`chunk_markdown`; everything else,
    including extensionless files, → :func:`chunk_code` with defaults.
    """
    suffix = PurePosixPath(source_path).suffix.lower()
    if suffix in _MARKDOWN_SUFFIXES:
        return chunk_markdown(text, source_path=source_path, file_hash=file_hash)
    return chunk_code(text, source_path=source_path, file_hash=file_hash)


# ----------------------------------------------------------------- internals


def _frontmatter_end(text: str) -> int:
    """Return the offset just past a leading YAML frontmatter block, or 0.

    A frontmatter block is ``---\\n…\\n---\\n`` at the very start of the file
    (``\\r\\n`` line endings also accepted). If the closing fence is missing,
    return 0 — treat the leading ``---`` as content.
    """
    m = _FRONTMATTER_RE.match(text)
    return m.end() if m else 0


def _format_path(stack: list[tuple[int, str]]) -> str:
    if not stack:
        return ""
    return "/" + "/".join(title for _, title in stack)


def _split_oversized(
    chunks: list[Chunk],
    *,
    max_chars: int = _MAX_CHUNK_CHARS,
    overlap: int = 0,
) -> list[Chunk]:
    """Split any chunk whose text exceeds ``max_chars``.

    Cuts prefer paragraph breaks (``\\n\\n``), then line breaks, then any
    whitespace, and finally a hard character split. With ``overlap`` > 0 each
    sub-chunk starts that many characters before the previous cut. Sub-chunks
    inherit ``heading_path``/``source_path``/``file_hash`` and have correct
    ``char_start``/``char_end`` offsets (round-trip invariant preserved per
    sub-chunk). ``chunk_index`` is renumbered sequentially across the list.
    """
    needs_split = any(len(c.text) > max_chars for c in chunks)
    if not needs_split:
        return chunks

    out: list[Chunk] = []
    for c in chunks:
        if len(c.text) <= max_chars:
            out.append(c)
        else:
            out.extend(_split_one(c, max_chars=max_chars, overlap=overlap))
    return [replace(c, chunk_index=i) for i, c in enumerate(out)]


def _split_one(c: Chunk, *, max_chars: int, overlap: int) -> list[Chunk]:
    text = c.text
    out: list[Chunk] = []
    pos = 0
    while pos < len(text):
        end = min(pos + max_chars, len(text))
        if end < len(text):
            cut = _backtrack_to_break(text, pos, end)
            if cut > pos:
                end = cut
        sub_text = text[pos:end]
        if not sub_text:
            break
        out.append(
            Chunk(
                source_path=c.source_path,
                file_hash=c.file_hash,
                chunk_index=0,  # renumbered by caller
                char_start=c.char_start + pos,
                char_end=c.char_start + end,
                heading_path=c.heading_path,
                text=sub_text,
                vector=[],
            )
        )
        if end == len(text):
            break
        # Guaranteed forward progress even when overlap >= the emitted span.
        pos = max(end - overlap, pos + 1)
    return out


def _backtrack_to_break(text: str, start: int, end: int) -> int:
    """Best cut position in ``(start, end]``: paragraph break > line break >
    whitespace > hard split at ``end``.

    Paragraph/line breaks are only accepted in the second half of the window
    so backtracking never produces a chunk under half of ``max_chars``; the
    whitespace fallback searches the whole window.
    """
    min_cut = start + (end - start) // 2
    para = text.rfind("\n\n", min_cut, end)
    if para != -1:
        return para + 2
    line = text.rfind("\n", min_cut, end)
    if line != -1:
        return line + 1
    for i in range(end, start, -1):
        if text[i - 1].isspace():
            return i
    return end
