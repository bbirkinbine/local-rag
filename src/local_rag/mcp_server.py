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

import hashlib
import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict, cast

import structlog
import uvicorn
from mcp.server.fastmcp import FastMCP

from local_rag.config import Config
from local_rag.store import Store

# ASGI 3.0 callable types — kept loose because the framework is third-party
# and we wrap it without committing to a specific app class.
_ASGIScope = dict[str, Any]
_ASGIReceive = Callable[[], Awaitable[dict[str, Any]]]
_ASGISend = Callable[[dict[str, Any]], Awaitable[None]]
_ASGIApp = Callable[[_ASGIScope, _ASGIReceive, _ASGISend], Awaitable[None]]

log = structlog.get_logger()

_K_MIN = 1
_K_MAX = 100


class _Embedder(Protocol):
    """Just the embedder surface this module needs."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def health_check(self) -> None: ...


# JSON-serializable shapes the tools return.


class SearchResult(TypedDict):
    score: float
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

    search: Callable[[str, list[str] | None, int], list[SearchResult]]
    list_sources: Callable[[], list[SourceInfo]]
    index_status: Callable[[], IndexStatus]


def build_tools(
    store: Store,
    embedder: _Embedder,
    configured_sources: list[str],
) -> Tools:
    """Bind the store/embedder/config to a ``Tools`` payload of pure functions."""

    def search(
        query: str, sources: list[str] | None, k: int
    ) -> list[SearchResult]:
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
        return [
            SearchResult(
                score=h.score,
                source_name=h.source_name,
                source_path=h.chunk.source_path,
                heading_path=h.chunk.heading_path,
                char_start=h.chunk.char_start,
                char_end=h.chunk.char_end,
                text=h.chunk.text,
            )
            for h in hits
        ]

    def list_sources() -> list[SourceInfo]:
        # Reports every table currently in the DB — including ones from a
        # previously-configured source that's since been removed from the
        # config. `search` with `sources=None` only searches *currently*
        # configured sources, so the two views can disagree by design.
        counts = store.chunk_counts()
        return [
            SourceInfo(name=name, chunk_count=counts[name]) for name in sorted(counts)
        ]

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
            "vault and indexed source repos. Use `search` for content "
            "lookup, `list_sources` to discover what's indexed, and "
            "`index_status` for chunk counts."
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


def run_http(
    config: Config,
    store: Store,
    embedder: _Embedder,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    token: str | None = None,
) -> None:
    """Start the MCP server over Streamable HTTP. Blocks until SIGINT/SIGTERM.

    Health-checks the embedder before binding (so a broken Ollama exits
    before we open the port). If ``token`` is provided, every incoming
    request must carry ``Authorization: Bearer <token>`` or it receives
    a 401. Token comparison is constant-time via :func:`hmac.compare_digest`.
    """
    embedder.health_check()
    tools = build_tools(
        store,
        embedder,
        configured_sources=[s.name for s in config.sources],
    )
    server = make_server(tools)
    # Starlette is structurally an ASGI app (callable with the right signature)
    # but mypy doesn't see the protocol match; cast to our alias.
    app: _ASGIApp = cast(_ASGIApp, server.streamable_http_app())
    if token:
        app = _bearer_auth_middleware(app, token)
    log.info(
        "mcp.http.starting",
        host=host,
        port=port,
        token_required=token is not None,
        tools=["search", "list_sources", "index_status"],
    )
    uvicorn.run(app, host=host, port=port, log_config=None)


def _bearer_auth_middleware(app: _ASGIApp, token: str) -> _ASGIApp:
    """Wrap ``app`` with a pure-ASGI middleware that enforces a bearer token.

    Non-HTTP scopes (lifespan, websocket) pass through unchanged — only
    HTTP requests are gated. Auth comparison is constant-time over a
    fixed-length SHA-256 digest of both sides, so neither the token's
    length nor any prefix can be inferred from response timing.
    """
    expected_digest = hashlib.sha256(token.encode()).digest()

    async def middleware(
        scope: _ASGIScope, receive: _ASGIReceive, send: _ASGISend
    ) -> None:
        if scope.get("type") != "http":
            await app(scope, receive, send)
            return

        header_value = b""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                header_value = value
                break

        if not header_value.startswith(b"Bearer "):
            await _send_401(send, b'{"error":"missing bearer token"}')
            return
        presented_digest = hashlib.sha256(header_value[len(b"Bearer ") :]).digest()
        if not hmac.compare_digest(presented_digest, expected_digest):
            await _send_401(send, b'{"error":"invalid bearer token"}')
            return

        await app(scope, receive, send)

    return middleware


async def _send_401(send: _ASGISend, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})
