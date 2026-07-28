"""The ``local-rag`` CLI entry point.

Four subcommands: ``index``, ``search``, ``list``, ``mcp``. User-facing
output goes to stdout; structlog log lines go to stderr (critical for
``mcp``, where stdout is the MCP JSON-RPC stream).

Built around small seams the tests poke at:
- :func:`_build_embedder` is the factory tests monkey-patch to inject a
  fake. Production wiring builds an :class:`OllamaEmbedder` from the config.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Protocol

import structlog

from local_rag.config import Config, ConfigError
from local_rag.embedder import EmbedderError, OllamaEmbedder
from local_rag.evals import EvalError, EvalReport, evaluate, load_golden_queries
from local_rag.indexer import Indexer, IndexResult
from local_rag.mcp_server import run_stdio
from local_rag.models import SearchHit
from local_rag.paths import default_config_path
from local_rag.rlimit import raise_open_file_limit
from local_rag.store import Store

log = structlog.get_logger()


class _CLIEmbedder(Protocol):
    """What the CLI needs from an embedder. Wider than ``indexer.Embedder``
    because the CLI also runs the Ollama health-check."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def health_check(self) -> None: ...


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv``, dispatch to the right subcommand, return an exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging()

    try:
        config = Config.load(args.config)
    except ConfigError as e:
        _err(f"config error: {e}")
        return 2

    store = Store(config.db_path, vector_dim=config.embedding.dim)

    if args.cmd == "list":
        return _cmd_list(store)
    if args.cmd == "index":
        return _cmd_index(config, store, args.sources, force=args.force)
    if args.cmd == "search":
        return _cmd_search(config, store, args.query, args.sources, args.k)
    if args.cmd == "eval":
        return _cmd_eval(config, store, args.golden, args.k)
    if args.cmd == "mcp":
        return _cmd_mcp(config, store)
    parser.error(f"unknown command {args.cmd!r}")
    return 2  # unreachable; argparse.error exits


# ----------------------------------------------------------------- commands


def _cmd_list(store: Store) -> int:
    counts = store.chunk_counts()
    if not counts:
        return 0
    for name in sorted(counts):
        print(f"{name}\t{counts[name]}")
    return 0


def _cmd_index(config: Config, store: Store, requested: list[str], *, force: bool = False) -> int:
    # Merging FTS deltas opens every index partition file at once; schedulers
    # hand out a 256 soft limit, well under what a mid-size table needs.
    raise_open_file_limit()

    by_name = {s.name: s for s in config.sources}
    if requested:
        unknown = [n for n in requested if n not in by_name]
        if unknown:
            _err(
                f"unknown source(s): {', '.join(unknown)}; configured: {', '.join(sorted(by_name))}"
            )
            return 2
        targets = [by_name[n] for n in requested]
    else:
        targets = list(config.sources)

    try:
        embedder = _build_embedder(config)
        embedder.health_check()
    except EmbedderError as e:
        _err(f"embedder unreachable: {e}")
        return 3

    indexer = Indexer(store, embedder)
    for source in targets:
        result = indexer.index_source(source, force=force)
        _print_index_summary(result)
    return 0


def _cmd_search(
    config: Config,
    store: Store,
    query: str,
    sources: list[str] | None,
    k: int,
) -> int:
    configured = {s.name for s in config.sources}
    if sources:
        unknown = [n for n in sources if n not in configured]
        if unknown:
            _err(
                f"unknown source(s): {', '.join(unknown)}; "
                f"configured: {', '.join(sorted(configured))}"
            )
            return 2
        requested = list(sources)
    else:
        requested = [s.name for s in config.sources]

    # Only sources with a built table can be searched; un-indexed configured
    # sources are silently dropped and surface as "no results" if nothing
    # remains.
    existing = [n for n in requested if n in store.list_sources()]

    try:
        embedder = _build_embedder(config)
        embedder.health_check()
    except EmbedderError as e:
        _err(f"embedder unreachable: {e}")
        return 3

    if not existing:
        print("no results")
        return 0

    vectors = embedder.embed([query])
    hits = store.search_hybrid(
        existing,
        query_text=query,
        query_vector=vectors[0],
        k=k,
    )
    if not hits:
        print("no results")
        return 0
    for hit in hits:
        _print_hit(hit)
    return 0


def _cmd_eval(config: Config, store: Store, golden_path: Path, k: int) -> int:
    try:
        queries = load_golden_queries(golden_path)
    except EvalError as e:
        _err(str(e))
        return 2

    existing = [s.name for s in config.sources if s.name in store.list_sources()]
    if not existing:
        _err("no indexed sources to evaluate against; run `local-rag index` first")
        return 2

    try:
        embedder = _build_embedder(config)
        embedder.health_check()
    except EmbedderError as e:
        _err(f"embedder unreachable: {e}")
        return 3

    report = evaluate(store, embedder, existing, queries, k=k)
    _print_eval_report(report)
    return 0


def _cmd_mcp(config: Config, store: Store) -> int:
    try:
        embedder = _build_embedder(config)
        run_stdio(config, store, embedder)
    except EmbedderError as e:
        _err(f"embedder unreachable: {e}")
        return 3
    return 0


# ------------------------------------------------------------------- output


def _print_index_summary(result: IndexResult) -> None:
    counts: dict[str, int] = {}
    for fr in result.files:
        counts[fr.status] = counts.get(fr.status, 0) + 1
    parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"{result.source_name}: {parts or 'nothing to do'}")


def _print_eval_report(report: EvalReport) -> None:
    for r in report.results:
        if r.query.is_negative:
            status = "neg-pass" if r.passed else "NEG-FAIL"
            print(
                f"{status}  {r.query.query!r}  max_cos={r.max_cosine or 0.0:.3f} "
                f"(limit {r.query.expect_max_cosine:.2f})"
            )
        elif r.best_rank is not None:
            print(f"rank={r.best_rank}  {r.query.query!r}  hit={r.matched_path}")
        else:
            expected = ", ".join(r.query.expected_paths)
            print(f"rank=-  {r.query.query!r}  miss (expected: {expected})")
    summary = (
        f"recall@{report.k}={report.recall:.3f}  mrr={report.mrr:.3f}  "
        f"queries={len(report.positives)}"
    )
    if report.negatives_total:
        summary += f"  negatives={report.negatives_passed}/{report.negatives_total}"
    print(summary)


def _print_hit(hit: SearchHit) -> None:
    header = f"score={hit.score:.3f}"
    if hit.cosine is not None:
        header += f"  cos={hit.cosine:.3f}"
    if hit.bm25 is not None:
        header += f"  bm25={hit.bm25:.2f}"
    header += f"  {hit.source_name}  {hit.chunk.source_path}"
    if hit.chunk.heading_path:
        header += f"  {hit.chunk.heading_path}"
    print(header)
    for line in hit.chunk.text.rstrip("\n").splitlines():
        print(f"    {line}")
    print()


# -------------------------------------------------------------- wiring


def _build_embedder(config: Config) -> _CLIEmbedder:
    """Production embedder factory. Tests monkey-patch this with a fake."""
    return OllamaEmbedder(
        url=config.embedding.url,
        model=config.embedding.model,
        dim=config.embedding.dim,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="local-rag")
    parser.add_argument(
        "--config",
        type=Path,
        default=_default_config(),
        help=f"path to config TOML (default: {default_config_path()})",
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    sub.add_parser("list", help="show sources and chunk counts")

    p_idx = sub.add_parser("index", help="reindex all sources or named ones")
    p_idx.add_argument("sources", nargs="*", metavar="SOURCE")
    p_idx.add_argument(
        "--force",
        action="store_true",
        help="re-chunk and re-embed every file, ignoring stored hashes",
    )

    p_search = sub.add_parser("search", help="hybrid search across sources")
    p_search.add_argument("query", metavar="QUERY")
    p_search.add_argument(
        "--sources",
        nargs="+",
        default=None,
        metavar="SOURCE",
        help="restrict to these source names (default: all configured)",
    )
    p_search.add_argument(
        "-k",
        type=int,
        default=10,
        help="top-k hits to return (default: 10)",
    )

    p_eval = sub.add_parser("eval", help="run the golden-query retrieval eval")
    p_eval.add_argument(
        "--golden",
        type=Path,
        default=Path("eval/golden.local.toml"),
        help="golden-query TOML file (default: eval/golden.local.toml)",
    )
    p_eval.add_argument(
        "-k",
        type=int,
        default=5,
        help="file-level cutoff for recall@k (default: 5)",
    )

    sub.add_parser("mcp", help="run the MCP server (stdio)")

    return parser


def _default_config() -> Path:
    env = os.environ.get("LOCAL_RAG_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    return default_config_path()


def _configure_logging() -> None:
    """Configure structlog for a CLI: stderr, one event per line, readable.

    The logger factory re-reads ``sys.stderr`` on every call so pytest's
    ``capsys`` (which swaps the stream between tests) keeps working.
    """

    def stderr_factory(*_args: object, **_kwargs: object) -> structlog.PrintLogger:
        return structlog.PrintLogger(sys.stderr)

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=stderr_factory,
        cache_logger_on_first_use=False,
    )


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)
