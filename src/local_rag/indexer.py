"""The indexer: walks a Source, chunks, embeds, upserts. Incremental via SHA-256.

Orchestrates the chunker → embedder → store pipeline for one `Source` at a
time. Uses the store's existing `file_hashes()` view to decide which files to
re-embed; unchanged files are skipped without ever touching the embedder.

Idempotent: running `index_source` twice on an unchanged tree is a no-op
(zero embedder calls, zero store writes).
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Protocol

import pathspec
import structlog

from local_rag.chunkers import chunk_file
from local_rag.config import Source
from local_rag.store import Store

log = structlog.get_logger()

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".md",
        ".mdx",
        ".txt",
        ".rst",
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".go",
        ".rs",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
    }
)

_DEFAULT_MAX_BYTES = 1_048_576

FileStatus = Literal[
    "embedded",
    "unchanged",
    "empty",
    "deleted",
    "skipped_oversize",
    "skipped_unreadable",
    "error",
]


@dataclass(frozen=True)
class _WalkEntry:
    """One file the source walk found. ``oversize=True`` means it cleared the
    extension allowlist but exceeded the byte cap."""

    path: Path
    oversize: bool


class Embedder(Protocol):
    """Minimal interface the indexer needs from the embedder."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class FileResult:
    path: Path
    status: FileStatus
    chunk_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class IndexResult:
    source_name: str
    files: list[FileResult]


class Indexer:
    """Sync orchestrator. One instance, many `index_source` calls."""

    def __init__(
        self,
        store: Store,
        embedder: Embedder,
        *,
        max_file_bytes: int = _DEFAULT_MAX_BYTES,
        allowed_extensions: frozenset[str] | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._max_file_bytes = max_file_bytes
        self._allowed_extensions = allowed_extensions or ALLOWED_EXTENSIONS

    def index_source(self, source: Source) -> IndexResult:
        """Sync disk → store for one source. Returns a per-file result list."""
        self._store.ensure_table(source.name)
        stored = self._store.file_hashes(source.name)

        results: list[FileResult] = []
        seen_paths: set[str] = set()

        # Single walk: oversize files still count as "seen" so we don't
        # mis-classify them as deleted orphans on the next reindex.
        for entry in _walk_entries(
            source,
            max_bytes=self._max_file_bytes,
            allowed_extensions=self._allowed_extensions,
        ):
            seen_paths.add(str(entry.path))
            if entry.oversize:
                results.append(FileResult(path=entry.path, status="skipped_oversize"))
            else:
                results.append(self._process_one(source.name, entry.path, stored))

        for stale in sorted(set(stored) - seen_paths):
            removed = self._store.delete_file(source.name, stale)
            log.info(
                "indexer.deleted",
                source=source.name,
                path=stale,
                chunks_removed=removed,
            )
            results.append(FileResult(path=Path(stale), status="deleted"))

        return IndexResult(source_name=source.name, files=results)

    def _process_one(
        self, source_name: str, file: Path, stored: dict[str, str]
    ) -> FileResult:
        try:
            file_hash = _file_sha256(file)
        except OSError as e:
            log.warning("indexer.unreadable", path=str(file), error=str(e))
            return FileResult(path=file, status="skipped_unreadable", error=str(e))

        if stored.get(str(file)) == file_hash:
            return FileResult(path=file, status="unchanged")

        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            log.warning("indexer.read_failed", path=str(file), error=str(e))
            return FileResult(path=file, status="skipped_unreadable", error=str(e))

        chunks = chunk_file(text, source_path=str(file), file_hash=file_hash)
        if not chunks:
            # Zero-chunk file (empty / whitespace-only). Nothing to embed; drop
            # any prior rows so the store mirrors current truth.
            self._store.delete_file(source_name, str(file))
            return FileResult(path=file, status="empty")

        try:
            vectors = self._embedder.embed([c.text for c in chunks])
        except Exception as e:
            log.exception("indexer.embed_failed", path=str(file))
            return FileResult(path=file, status="error", error=str(e))

        if len(vectors) != len(chunks):
            err = (
                f"embedder returned {len(vectors)} vectors for {len(chunks)} chunks"
            )
            log.warning("indexer.embed_shape", path=str(file), error=err)
            return FileResult(path=file, status="error", error=err)

        embedded = [replace(c, vector=v) for c, v in zip(chunks, vectors, strict=True)]

        try:
            # Replace prior chunks atomically: delete first, then upsert.
            self._store.delete_file(source_name, str(file))
            self._store.upsert_chunks(source_name, embedded)
        except Exception as e:
            log.exception("indexer.store_failed", path=str(file))
            return FileResult(path=file, status="error", error=str(e))

        log.info(
            "indexer.embedded",
            source=source_name,
            path=str(file),
            chunks=len(embedded),
        )
        return FileResult(path=file, status="embedded", chunk_count=len(embedded))


# ----------------------------------------------------------------- internals


def _file_sha256(path: Path) -> str:
    """SHA-256 of file contents, streamed in 64KB blocks."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk_entries(
    source: Source,
    *,
    max_bytes: int,
    allowed_extensions: frozenset[str] = ALLOWED_EXTENSIONS,
) -> Iterator[_WalkEntry]:
    """One pass over the source. Yields every allowlisted file once.

    Files exceeding ``max_bytes`` get ``oversize=True`` so the caller can
    report them without losing track that they exist (preventing them from
    being misclassified as deleted orphans on the next reindex).
    Off-allowlist files (binaries, unknown extensions) are silently skipped.
    """
    for path in _walk(source):
        if path.suffix.lower() not in allowed_extensions:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        yield _WalkEntry(path=path, oversize=size >= max_bytes)


def _iter_source_files(
    source: Source,
    *,
    max_bytes: int,
    allowed_extensions: frozenset[str] = ALLOWED_EXTENSIONS,
) -> Iterator[Path]:
    """Yield non-oversize, allowlisted files. Thin wrapper around ``_walk_entries``."""
    for entry in _walk_entries(
        source, max_bytes=max_bytes, allowed_extensions=allowed_extensions
    ):
        if not entry.oversize:
            yield entry.path


def _walk(source: Source) -> Iterator[Path]:
    """Walk the source root subject to ignore/gitignore policy."""
    root = source.path
    if source.respect_gitignore and (root / ".git").is_dir():
        yield from _walk_via_git_ls_files(root, source.ignore)
        return
    if source.respect_gitignore:
        log.warning(
            "indexer.gitignore_unavailable",
            path=str(root),
            reason="not a git repo; falling back to plain walk",
        )
    yield from _walk_plain(root, source.ignore)


def _walk_plain(root: Path, ignore_globs: list[str]) -> Iterator[Path]:
    spec = pathspec.GitIgnoreSpec.from_lines(ignore_globs) if ignore_globs else None
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if spec is not None and spec.match_file(rel):
            continue
        yield path


def _walk_via_git_ls_files(root: Path, ignore_globs: list[str]) -> Iterator[Path]:
    """Defer the walk to git so nested .gitignore + .git/info/exclude are honored.

    Falls back to ``_walk_plain`` if git can't be invoked (binary missing,
    corrupt repo, etc).
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as e:
        log.warning("indexer.git_ls_files_failed", path=str(root), error=str(e))
        yield from _walk_plain(root, ignore_globs)
        return

    spec = pathspec.GitIgnoreSpec.from_lines(ignore_globs) if ignore_globs else None
    for rel_bytes in out.stdout.split(b"\x00"):
        if not rel_bytes:
            continue
        rel = rel_bytes.decode("utf-8", errors="replace")
        if spec is not None and spec.match_file(rel):
            continue
        path = root / rel
        if path.is_file():
            yield path
