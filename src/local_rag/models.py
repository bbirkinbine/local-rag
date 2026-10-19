"""Canonical data shapes shared across the indexer, store, and search code."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Chunk:
    """A single chunk of source text plus its embedding, ready for storage.

    The ``(source_path, chunk_index)`` pair is the stable identity used as the
    upsert key. ``file_hash`` is the SHA-256 of the source file's content
    (used to skip re-embedding unchanged files during incremental reindex).

    ``note_date``, ``status``, and ``tags`` come from a markdown note's
    frontmatter (absent for code and for notes without it). ``kind`` names
    the chunk type; every chunk is ``"text"`` today.
    """

    source_path: str
    file_hash: str
    chunk_index: int
    char_start: int
    char_end: int
    heading_path: str
    text: str
    vector: list[float]
    note_date: date | None = None
    status: str | None = None
    tags: tuple[str, ...] = ()
    kind: str = "text"

    @property
    def id(self) -> str:
        return f"{self.source_path}::{self.chunk_index}"


@dataclass(frozen=True)
class SearchHit:
    """A search result. ``score`` is uniformly higher-is-better across rankers.

    ``score`` is the final ranking value: for hybrid search, cosine plus a
    small saturating keyword boost; for vector-only search, plain cosine.
    ``cosine`` and ``bm25`` are the raw signals behind it — ``cosine`` is the
    value to threshold on for hit strength, ``bm25`` is ``None`` when the
    chunk had no lexical match.
    """

    source_name: str
    chunk: Chunk
    score: float
    cosine: float | None = None
    bm25: float | None = None
