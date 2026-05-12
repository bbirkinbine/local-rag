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
from pathlib import PurePosixPath

from local_rag.models import Chunk

_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6}) +(.+?)\s*$")
_MARKDOWN_SUFFIXES = frozenset({".md"})


def chunk_markdown(text: str, *, source_path: str, file_hash: str) -> list[Chunk]:
    """Header-aware markdown chunking.

    Splits on ATX headings (``^#{1,6} ``). Each chunk contains the heading line
    that introduces it plus all content up to the next heading. Pre-heading
    body, if any, emits its own chunk with ``heading_path=""``.

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
    return chunks


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
            f"window_lines ({window_lines}) must be greater than "
            f"overlap_lines ({overlap_lines})"
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

    return chunks


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
