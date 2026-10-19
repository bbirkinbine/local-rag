"""JSON-serializable shapes the MCP tools return."""

from __future__ import annotations

from typing import TypedDict

from local_rag.run_status import FailureGroup


class SearchResult(TypedDict):
    score: float
    cosine: float | None
    bm25: float | None
    source_name: str
    source_path: str
    heading_path: str
    char_start: int
    char_end: int
    text: str
    note_date: str | None
    status: str | None
    tags: list[str]
    kind: str


class SourceInfo(TypedDict):
    name: str
    chunk_count: int


class SourceStatus(TypedDict):
    name: str
    chunk_count: int
    last_run: str | None
    last_run_age_seconds: int | None
    last_change: str | None
    last_attempt: str | None
    last_error: str | None
    embedding_model: str | None
    failures: list[FailureGroup]


class IndexStatus(TypedDict):
    total_chunks: int
    sources: list[SourceStatus]
