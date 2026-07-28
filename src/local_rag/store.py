"""LanceDB-backed chunk store with hybrid (vector + FTS) search.

One table per source; row schema fixed at table creation. No vector index in
v1 (brute-force cosine scan is fine for <100k chunks per the project spec).
A native LanceDB FTS index is built on the ``text`` column at table creation
so hybrid search works without a separate setup step.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa
import structlog

from local_rag.models import Chunk, SearchHit

log = structlog.get_logger()

# Reciprocal Rank Fusion constant. 60 is the BM25/RRF folklore default.
_RRF_K = 60

# Upper bound on FTS match rows scanned per query when recovering the true
# top-n (see Store._fts_top). Effectively "all matches" — tables are <100k
# rows in v1 — while still bounding a pathological query.
_FTS_SCAN_LIMIT = 1_000_000

# Lexical blend: final ranking value = cosine + WEIGHT * bm25/(bm25 + PIVOT).
# The boost saturates below WEIGHT, so a strong exact-keyword match wins
# cosine near-ties (identifier/title lookups sit ~0.02-0.05 behind topical
# noise; see eval 2026-07-09) but can never bridge a large semantic gap.
# PIVOT is the bm25 score at which half the boost is earned (live-index
# lexical hits score ~5-22).
_BM25_BLEND_WEIGHT = 0.15
_BM25_BLEND_PIVOT = 10.0

# How many past indexing runs stay recoverable. LanceDB retains every table
# version indefinitely otherwise — 809 versions / 7 GB of fragments had built
# up on a ~37k-chunk store by 2026-07-27, and the file sprawl exhausted the
# indexing process's FD limit mid-merge.
#
# Bounded in runs rather than elapsed time because old copies are produced per
# run and nothing is produced between runs: both the disk cost and the undo
# depth are functions of run count, and neither is a function of the clock. A
# time window would mean different things on different schedules — 24 runs of
# undo at a 30-minute cadence, none at all on a nightly one.
#
# 24 matches what a 12-hour window bought on the documented 30-minute
# schedule: ~1.3 GB on a 37k-chunk vault. Recovery is all it buys — the store
# is fully derived, and anything pruned is rebuildable with `index --force`.
DEFAULT_KEEP_RUNS = 24

# Filename of the run log, kept beside the LanceDB tables. Run boundaries
# can't be recovered from version timestamps: one run emits several versions
# (~6.6 on a 37k-chunk table), and a slow run can outlast the gap between
# fast ones, so no timestamp-gap threshold separates them reliably.
_RUN_LOG_NAME = ".run_log.json"

# Age passed to LanceDB when every version must survive. It only accepts a
# window, so "keep everything" is spelled as a window nothing can exceed.
_KEEP_EVERYTHING = timedelta(days=36_500)


def _utc_now() -> datetime:
    """Current UTC time. Wrapped so tests can pin it."""
    return datetime.now(UTC)


def _cutoff_age(history: list[str], keep_runs: int) -> timedelta:
    """Translate "keep the last N runs" into the age LanceDB wants.

    Args:
        history: Run-completion timestamps, oldest first, ISO-8601.
        keep_runs: Runs that must stay recoverable.

    Returns:
        Age beyond which versions may be pruned. ``_KEEP_EVERYTHING`` when
        the history is too short to know where run N-ago ended, so a missing
        or truncated log can never license a prune.
    """
    if keep_runs <= 0:
        return timedelta(0)
    if len(history) < keep_runs:
        return _KEEP_EVERYTHING

    boundary = history[-keep_runs]
    try:
        started = datetime.fromisoformat(boundary)
    except ValueError:
        log.warning("store.run_log_bad_timestamp", value=boundary)
        return _KEEP_EVERYTHING

    # Clamped at zero: a clock that jumped backwards must not become a
    # negative age, which LanceDB would read as "prune the future".
    return max(_utc_now() - started, timedelta(0))


def _bm25_boost(bm25: float | None) -> float:
    """Saturating lexical boost in ``[0, _BM25_BLEND_WEIGHT)``; 0 for no match."""
    if bm25 is None or bm25 <= 0.0:
        return 0.0
    return _BM25_BLEND_WEIGHT * bm25 / (bm25 + _BM25_BLEND_PIVOT)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Plain cosine similarity; 0.0 if either vector is all zeros."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class Store:
    """LanceDB chunk store. One table per source, hybrid search via RRF."""

    def __init__(
        self,
        db_path: Path,
        vector_dim: int,
        *,
        keep_runs: int = DEFAULT_KEEP_RUNS,
    ) -> None:
        """Open (or create) a LanceDB at ``db_path``.

        Args:
            db_path: Directory holding the LanceDB tables; created if absent.
            vector_dim: Embedding width. All tables share it.
            keep_runs: How many past indexing runs stay recoverable after
                :meth:`optimize` prunes.
        """
        db_path.mkdir(parents=True, exist_ok=True)
        # `Any`: lancedb has no published type stubs / py.typed marker.
        self._db: Any = lancedb.connect(str(db_path))
        self._vector_dim = vector_dim
        self._keep_runs = keep_runs
        self._run_log_path = db_path / _RUN_LOG_NAME

    @property
    def keep_runs(self) -> int:
        """How many past indexing runs survive :meth:`optimize`."""
        return self._keep_runs

    @property
    def run_log_path(self) -> Path:
        """Where run boundaries are recorded, beside the LanceDB tables."""
        return self._run_log_path

    # -------------------------------------------------------------- schema

    def _schema(self) -> pa.Schema:
        return pa.schema(
            [
                pa.field("id", pa.string(), nullable=False),
                pa.field("source_path", pa.string(), nullable=False),
                pa.field("file_hash", pa.string(), nullable=False),
                pa.field("chunk_index", pa.int64(), nullable=False),
                pa.field("char_start", pa.int64(), nullable=False),
                pa.field("char_end", pa.int64(), nullable=False),
                pa.field("heading_path", pa.string(), nullable=False),
                pa.field("text", pa.string(), nullable=False),
                pa.field(
                    "vector",
                    pa.list_(pa.float32(), self._vector_dim),
                    nullable=False,
                ),
            ]
        )

    # ----------------------------------------------------------- table ops

    def ensure_table(self, source_name: str) -> None:
        """Create the table for this source if it doesn't exist. Idempotent."""
        if source_name in self._table_names():
            return
        tbl = self._db.create_table(source_name, schema=self._schema())
        # Native LanceDB FTS — auto-updates on subsequent upserts.
        tbl.create_fts_index("text", use_tantivy=False, replace=True)

    def optimize(self, source_name: str, *, keep_runs: int | None = None) -> None:
        """Merge table/index deltas, then prune all but the last N runs.

        LanceDB 0.30's native FTS misbehaves against unmerged ``merge_insert``
        deltas: ``limit(n)`` returns an arbitrary unsorted sample instead of
        the top-n by score, per-row scores are computed from stale corpus
        statistics, and for some corpora matching rows are hidden entirely
        (all verified 2026-07-09). ``optimize()`` merges the deltas and
        restores exact, ordered results. Costs seconds on a ~30k-row table —
        call once per indexing run, not per upsert or per query.

        Merging supersedes the previous version but does not delete it, so the
        same call prunes history back to the start of the run ``keep_runs``
        ago. Live rows are never touched — only history. This call *is* the
        run boundary: it records one entry in the run log.

        LanceDB only accepts an age, so the run count is translated into one
        here. With fewer runs on record than requested, nothing is pruned —
        the fail-safe direction when the log is short, missing, or damaged.

        Args:
            source_name: Table to optimize.
            keep_runs: Runs to keep recoverable. Defaults to this store's
                ``keep_runs``. Pass ``0`` to keep only what LanceDB cannot
                reclaim.
        """
        wanted = self._keep_runs if keep_runs is None else keep_runs
        log = self._read_run_log()
        history = log.get(source_name, [])

        self._db.open_table(source_name).optimize(cleanup_older_than=_cutoff_age(history, wanted))

        # Record after pruning: the entry describes a run that is now complete.
        history.append(_utc_now().isoformat())
        # One spare beyond the window so the oldest kept run has a boundary.
        log[source_name] = history[-(wanted + 1) :] if wanted >= 0 else history
        self._write_run_log(log)

    def _read_run_log(self) -> dict[str, list[str]]:
        """Return the run log, or an empty log if it is absent or unreadable.

        A damaged log must never abort indexing or, worse, license a prune:
        callers treat "no history" as "keep everything".
        """
        try:
            raw = json.loads(self._run_log_path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as e:
            log.warning("store.run_log_unreadable", path=str(self._run_log_path), error=str(e))
            return {}
        if not isinstance(raw, dict):
            log.warning("store.run_log_malformed", path=str(self._run_log_path))
            return {}
        return {k: list(v) for k, v in raw.items() if isinstance(v, list)}

    def _write_run_log(self, log_data: dict[str, list[str]]) -> None:
        """Replace the run log atomically; a write failure is not fatal."""
        tmp = self._run_log_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(log_data, indent=2))
            tmp.replace(self._run_log_path)
        except OSError as e:
            log.warning("store.run_log_unwritable", path=str(self._run_log_path), error=str(e))

    def list_sources(self) -> list[str]:
        """Return the names of all tables in the DB, sorted alphabetically."""
        return sorted(self._table_names())

    def chunk_counts(self) -> dict[str, int]:
        """Return ``{source_name: row_count}`` across every table in the DB."""
        return {name: int(self._db.open_table(name).count_rows()) for name in self._table_names()}

    def file_hashes(self, source_name: str) -> dict[str, str]:
        """Return ``{source_path: file_hash}`` for every distinct file in the table.

        All chunks of one file share the same hash, so we collapse to one entry
        per ``source_path``. Used by the indexer to detect unchanged files and
        skip re-embedding.
        """
        tbl = self._db.open_table(source_name)
        arrow_tbl = tbl.to_arrow().select(["source_path", "file_hash"])
        out: dict[str, str] = {}
        for row in arrow_tbl.to_pylist():
            out.setdefault(str(row["source_path"]), str(row["file_hash"]))
        return out

    def _table_names(self) -> list[str]:
        # LanceDB >=0.30 returns a paginated ListTablesResponse; .tables is
        # the flat list we want.
        return list(self._db.list_tables().tables)

    # --------------------------------------------------------- upsert / delete

    def upsert_chunks(self, source_name: str, chunks: list[Chunk]) -> None:
        """Insert or update ``chunks`` keyed by ``Chunk.id``. Empty input is a no-op.

        Raises:
            ValueError: if any chunk's vector length does not match ``vector_dim``.
        """
        if not chunks:
            return
        tbl = self._db.open_table(source_name)
        rows = [self._chunk_to_row(c) for c in chunks]
        (
            tbl.merge_insert("id")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute(rows)
        )

    def delete_file(self, source_name: str, source_path: str) -> int:
        """Delete every row for ``source_path``. Returns the number removed."""
        tbl = self._db.open_table(source_name)
        before = int(tbl.count_rows())
        escaped = source_path.replace("'", "''")
        tbl.delete(f"source_path = '{escaped}'")
        after = int(tbl.count_rows())
        return before - after

    # -------------------------------------------------------------- search

    def search_vector(
        self,
        source_names: list[str],
        query_vector: list[float],
        k: int,
    ) -> list[SearchHit]:
        """Cosine-similarity search across ``source_names``. Returns top-k overall.

        Per the v1 project rule, no vector index is built — this is a brute-force
        scan per table, then a global top-k merge. Empty ``source_names`` returns ``[]``.
        """
        if not source_names:
            return []
        hits: list[SearchHit] = []
        for name in source_names:
            tbl = self._db.open_table(name)
            rows = tbl.search(query_vector).distance_type("cosine").limit(k).to_list()
            for row in rows:
                score = 1.0 - float(row["_distance"])  # cosine dist -> similarity
                hits.append(
                    SearchHit(
                        source_name=name,
                        chunk=self._row_to_chunk(row),
                        score=score,
                        cosine=score,
                    )
                )
        hits.sort(key=lambda h: -h.score)
        return hits[:k]

    def search_hybrid(
        self,
        source_names: list[str],
        query_text: str,
        query_vector: list[float],
        k: int,
    ) -> list[SearchHit]:
        """Hybrid search: both modalities gather candidates; cosine plus a
        bounded lexical boost orders them.

        Both modalities contribute candidates (top ``max(4*k, 50)`` each per
        source). The final ranking value is ``cosine + boost(bm25)`` where the
        boost saturates below ``_BM25_BLEND_WEIGHT``: raw RRF ordering let
        keyword-dense chunks with weak semantic similarity beat strongly
        similar notes, while cosine-only ordering made exact-keyword lookups
        (identifiers, note titles) unreachable — the blend fixes both
        (2026-07-09 eval). RRF rank breaks exact ties.

        Each hit carries ``score`` (the final ranking value), plus the raw
        signals ``cosine`` and ``bm25`` (``None`` without a lexical match).
        """
        if not source_names:
            return []

        expand = max(4 * k, 50)
        rrf: dict[str, float] = {}
        chunks: dict[str, tuple[str, Chunk]] = {}
        bm25: dict[str, float] = {}

        for name in source_names:
            tbl = self._db.open_table(name)

            for rank, row in enumerate(
                tbl.search(query_vector).distance_type("cosine").limit(expand).to_list(),
                start=1,
            ):
                key = f"{name}::{row['id']}"
                rrf[key] = rrf.get(key, 0.0) + 1.0 / (_RRF_K + rank)
                chunks.setdefault(key, (name, self._row_to_chunk(row)))

            for rank, row in enumerate(self._fts_top(tbl, query_text, expand), start=1):
                key = f"{name}::{row['id']}"
                rrf[key] = rrf.get(key, 0.0) + 1.0 / (_RRF_K + rank)
                chunks.setdefault(key, (name, self._row_to_chunk(row)))
                bm25[key] = float(row["_score"])

        cosine = {
            key: _cosine_similarity(query_vector, chunk.vector)
            for key, (_, chunk) in chunks.items()
        }
        final = {key: cosine[key] + _bm25_boost(bm25.get(key)) for key in chunks}
        ranked = sorted(rrf, key=lambda key: (-final[key], -rrf[key]))[:k]
        return [
            SearchHit(
                source_name=chunks[key][0],
                chunk=chunks[key][1],
                score=final[key],
                cosine=cosine[key],
                bm25=bm25.get(key),
            )
            for key in ranked
        ]

    # `Any`: lancedb table handle; no published type stubs (same as self._db).
    def _fts_top(self, tbl: Any, query_text: str, n: int) -> list[dict[str, Any]]:  # noqa: ANN401
        """True top-n FTS matches by BM25 score, best first.

        LanceDB 0.30's native FTS returns ``limit(n)`` as an arbitrary
        unsorted sample of matching rows, not the top-n by score (verified
        2026-07-09 against a live 30k-row table: zero overlap with the true
        top-n). Work around it in two phases: fetch ``(id, _score)`` for
        every match (cheap — no text/vector payload), sort here, then fetch
        the full rows for the true top-n by id.
        """
        light = (
            tbl.search(query_text, query_type="fts").select(["id"]).limit(_FTS_SCAN_LIMIT).to_list()
        )
        if not light:
            return []
        light.sort(key=lambda r: -float(r["_score"]))
        scores = {str(r["id"]): float(r["_score"]) for r in light[:n]}
        id_list = ", ".join("'" + i.replace("'", "''") + "'" for i in scores)
        rows = tbl.search().where(f"id IN ({id_list})").limit(len(scores)).to_list()
        for row in rows:
            row["_score"] = scores[str(row["id"])]
        rows.sort(key=lambda r: -float(r["_score"]))
        return list(rows)

    def expanded_text(
        self,
        source_name: str,
        source_path: str,
        *,
        center_index: int,
        radius: int,
    ) -> tuple[str, int, int]:
        """Stitch the chunk at ``center_index`` with up to ``radius`` neighbors
        on each side into one contiguous span of the source file.

        Overlapping neighbor ranges (the markdown cap's overlap) are merged
        via their char offsets so no text is duplicated; a gap between chunks
        (shouldn't occur within one file) falls back to a newline join.

        Returns:
            ``(text, char_start, char_end)`` of the stitched span.

        Raises:
            ValueError: if no chunks exist for ``source_path`` around
                ``center_index``.
        """
        tbl = self._db.open_table(source_name)
        escaped = source_path.replace("'", "''")
        lo, hi = center_index - radius, center_index + radius
        rows = (
            tbl.search()
            .where(f"source_path = '{escaped}' AND chunk_index >= {lo} AND chunk_index <= {hi}")
            .limit(hi - lo + 1)
            .to_list()
        )
        if not rows:
            raise ValueError(f"no chunks for {source_path!r} around index {center_index}")
        rows.sort(key=lambda r: int(r["chunk_index"]))

        text = str(rows[0]["text"])
        start = int(rows[0]["char_start"])
        end = int(rows[0]["char_end"])
        for row in rows[1:]:
            row_start, row_end = int(row["char_start"]), int(row["char_end"])
            row_text = str(row["text"])
            if row_end <= end:
                continue  # fully contained in what we already have
            if row_start <= end:
                text += row_text[end - row_start :]
            else:
                text += "\n" + row_text
            end = row_end
        return text, start, end

    # ------------------------------------------------------------- mapping

    def _chunk_to_row(self, c: Chunk) -> dict[str, Any]:
        if len(c.vector) != self._vector_dim:
            raise ValueError(
                f"chunk {c.id}: vector dim mismatch "
                f"(expected {self._vector_dim}, got {len(c.vector)})"
            )
        return {
            "id": c.id,
            "source_path": c.source_path,
            "file_hash": c.file_hash,
            "chunk_index": c.chunk_index,
            "char_start": c.char_start,
            "char_end": c.char_end,
            "heading_path": c.heading_path,
            "text": c.text,
            "vector": c.vector,
        }

    def _row_to_chunk(self, row: dict[str, Any]) -> Chunk:
        return Chunk(
            source_path=str(row["source_path"]),
            file_hash=str(row["file_hash"]),
            chunk_index=int(row["chunk_index"]),
            char_start=int(row["char_start"]),
            char_end=int(row["char_end"]),
            heading_path=str(row["heading_path"]),
            text=str(row["text"]),
            vector=[float(x) for x in row["vector"]],
        )
