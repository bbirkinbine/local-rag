"""MCP server: exposes search/list_sources/index_status to MCP clients.

The tool *functions* (in :class:`Tools`) are plain Python — built with
:func:`build_tools` and easy to test directly. :func:`make_server` wraps
them in FastMCP's ``@tool`` decorators. :func:`run_stdio` is the production
entry point used by ``local-rag mcp``: it runs the embedder health-check
first (so a broken Ollama surfaces clearly) and then blocks on stdio.

Stdio safety: this module must never write to stdout — that stream is the
MCP JSON-RPC channel. All log output goes via structlog → stderr (the CLI
configures structlog before calling :func:`run_stdio`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypedDict

import structlog
from mcp.server.fastmcp import FastMCP

from local_rag.config import Config
from local_rag.store import Store

log = structlog.get_logger()

_K_MIN = 1
_K_MAX = 100
_CONTEXT_CHUNKS_MAX = 5


class _Embedder(Protocol):
    """Just the embedder surface this module needs."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def health_check(self) -> None: ...


# JSON-serializable shapes the tools return.


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


class SourceInfo(TypedDict):
    name: str
    chunk_count: int


class IndexStatus(TypedDict):
    total_chunks: int
    sources: list[SourceInfo]


@dataclass(frozen=True)
class Tools:
    """The three callables FastMCP wires up. Plain functions for testability."""

    search: Callable[[str, list[str] | None, int, int], list[SearchResult]]
    list_sources: Callable[[], list[SourceInfo]]
    index_status: Callable[[], IndexStatus]


def build_tools(
    store: Store,
    embedder: _Embedder,
    configured_sources: list[str],
) -> Tools:
    """Bind the store/embedder/config to a ``Tools`` payload of pure functions."""

    def search(
        query: str,
        sources: list[str] | None,
        k: int,
        context_chunks: int = 0,
    ) -> list[SearchResult]:
        """Hybrid semantic + keyword search over the user's indexed sources.

        Reach for this instead of grep/file search when (a) you want matches
        by meaning rather than shared keywords, or (b) the content lives
        outside the current workspace — the Obsidian vault from a coding
        session, or indexed repos from a notes session. For exact-string
        lookup inside files you can already read, built-in grep is better.

        Scoring: results are ordered by `score` — raw cosine similarity plus
        a small bounded keyword boost, so exact-term lookups (identifiers,
        note titles) win near-ties. Judge hit strength with `cosine` (treat
        a sharp drop-off as the end of the relevant results); `bm25` is the
        raw keyword score (null when the chunk had no lexical match).

        Set `context_chunks` (0-5) to widen each hit with that many
        neighboring chunks on each side of the match — useful when the
        matched chunk alone is too little context and you can't read the
        source file directly.
        """
        if not query.strip():
            return []
        effective_k = max(_K_MIN, min(k, _K_MAX))
        # sources=None → "use defaults" (all configured); sources=[] → "the
        # caller explicitly asked for nothing" → empty result.
        requested = list(configured_sources) if sources is None else list(sources)
        existing = [n for n in requested if n in store.list_sources()]
        if not existing:
            return []
        query_vec = embedder.embed([query])[0]
        hits = store.search_hybrid(
            existing,
            query_text=query,
            query_vector=query_vec,
            k=effective_k,
        )
        radius = max(0, min(context_chunks, _CONTEXT_CHUNKS_MAX))
        results: list[SearchResult] = []
        for h in hits:
            text = h.chunk.text
            char_start, char_end = h.chunk.char_start, h.chunk.char_end
            if radius > 0:
                try:
                    text, char_start, char_end = store.expanded_text(
                        h.source_name,
                        h.chunk.source_path,
                        center_index=h.chunk.chunk_index,
                        radius=radius,
                    )
                except ValueError:
                    log.warning(
                        "mcp.context_expansion_failed",
                        source=h.source_name,
                        path=h.chunk.source_path,
                        chunk_index=h.chunk.chunk_index,
                    )
            results.append(
                SearchResult(
                    score=h.score,
                    cosine=h.cosine,
                    bm25=h.bm25,
                    source_name=h.source_name,
                    source_path=h.chunk.source_path,
                    heading_path=h.chunk.heading_path,
                    char_start=char_start,
                    char_end=char_end,
                    text=text,
                )
            )
        return results

    def list_sources() -> list[SourceInfo]:
        # Reports every table currently in the DB — including ones from a
        # previously-configured source that's since been removed from the
        # config. `search` with `sources=None` only searches *currently*
        # configured sources, so the two views can disagree by design.
        counts = store.chunk_counts()
        return [SourceInfo(name=name, chunk_count=counts[name]) for name in sorted(counts)]

    def index_status() -> IndexStatus:
        sources = list_sources()
        total = sum(s["chunk_count"] for s in sources)
        return IndexStatus(total_chunks=total, sources=sources)

    return Tools(search=search, list_sources=list_sources, index_status=index_status)


def make_server(tools: Tools) -> FastMCP:
    """Wrap ``tools`` in a FastMCP server with the three tool registrations."""
    server: FastMCP = FastMCP(
        name="local-rag",
        instructions=(
            "Local semantic + keyword search across the user's Obsidian "
            "vault and indexed source repos. `search` beats built-in "
            "grep when the match is conceptual (no shared keywords) or "
            "the content is outside the current workspace; use "
            "`list_sources` to discover what's indexed and `index_status` "
            "for chunk counts. Hits include `cosine` (strength signal) "
            "and `bm25` (keyword signal) alongside the fusion rank score."
        ),
    )

    server.tool(name="search")(tools.search)
    server.tool(name="list_sources")(tools.list_sources)
    server.tool(name="index_status")(tools.index_status)

    return server


def run_stdio(config: Config, store: Store, embedder: _Embedder) -> None:
    """Production entry point: health-check, build tools, run the stdio loop.

    Blocks until the MCP client disconnects. Caller is responsible for
    propagating any :class:`EmbedderError` raised here as exit code 3.
    """
    embedder.health_check()
    tools = build_tools(
        store,
        embedder,
        configured_sources=[s.name for s in config.sources],
    )
    server = make_server(tools)
    log.info("mcp.stdio.starting", tools=["search", "list_sources", "index_status"])
    server.run(transport="stdio")
