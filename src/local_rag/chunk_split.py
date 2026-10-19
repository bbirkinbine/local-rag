"""Split chunks that exceed a character cap, preserving source offsets.

Shared by both chunkers: a hard cap that keeps every chunk under the
embedder's limit, and (for markdown) a tighter retrieval-quality cap with
overlap.
"""

from __future__ import annotations

from dataclasses import replace

from local_rag.models import Chunk

# Soft cap on per-chunk character count. The embedder enforces a hard
# 30,000-char limit (bge-m3's ~8192-token window with headroom); we leave
# 6,000 chars between the two so a chunk that almost-hits this cap doesn't
# blow up at the embedder boundary. Long-line files (transcripts, minified
# code, generated logs) routinely have single "lines" of 30k+ chars; without
# this cap they'd produce one chunk per file and fail.
_MAX_CHUNK_CHARS = 24_000


def split_oversized(
    chunks: list[Chunk],
    *,
    max_chars: int = _MAX_CHUNK_CHARS,
    overlap: int = 0,
) -> list[Chunk]:
    """Split any chunk whose text exceeds ``max_chars``.

    Cuts prefer paragraph breaks (``\\n\\n``), then line breaks, then any
    whitespace, and finally a hard character split. With ``overlap`` > 0 each
    sub-chunk starts that many characters before the previous cut. Sub-chunks
    inherit every other field (``heading_path``, metadata, ...) and have correct
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
            replace(
                c,
                chunk_index=0,  # renumbered by caller
                char_start=c.char_start + pos,
                char_end=c.char_start + end,
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
