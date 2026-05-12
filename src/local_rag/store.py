"""LanceDB-backed chunk store with hybrid (vector + FTS) search.

One table per source; row schema fixed at table creation. No vector index in
v1 (brute-force cosine scan is fine for <100k chunks per the project spec).
A native LanceDB FTS index is built on the ``text`` column at table creation
so hybrid search works without a separate setup step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa

from local_rag.models import Chunk, SearchHit

# Reciprocal Rank Fusion constant. 60 is the BM25/RRF folklore default.
_RRF_K = 60


class Store:
    """LanceDB chunk store. One table per source, hybrid search via RRF."""

    def __init__(self, db_path: Path, vector_dim: int) -> None:
        """Open (or create) a LanceDB at ``db_path``. All tables share ``vector_dim``."""
        db_path.mkdir(parents=True, exist_ok=True)
        # `Any`: lancedb has no published type stubs / py.typed marker.
        self._db: Any = lancedb.connect(str(db_path))
        self._vector_dim = vector_dim

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
        """Vector + FTS fused via Reciprocal Rank Fusion."""
        if not source_names:
            return []

        expand = max(4 * k, 50)
        rrf: dict[str, float] = {}
        chunks: dict[str, tuple[str, Chunk]] = {}

        for name in source_names:
            tbl = self._db.open_table(name)

            for rank, row in enumerate(
                tbl.search(query_vector).distance_type("cosine").limit(expand).to_list(),
                start=1,
            ):
                key = f"{name}::{row['id']}"
                rrf[key] = rrf.get(key, 0.0) + 1.0 / (_RRF_K + rank)
                chunks.setdefault(key, (name, self._row_to_chunk(row)))

            for rank, row in enumerate(
                tbl.search(query_text, query_type="fts").limit(expand).to_list(),
                start=1,
            ):
                key = f"{name}::{row['id']}"
                rrf[key] = rrf.get(key, 0.0) + 1.0 / (_RRF_K + rank)
                chunks.setdefault(key, (name, self._row_to_chunk(row)))

        ranked = sorted(rrf.items(), key=lambda kv: -kv[1])[:k]
        return [
            SearchHit(source_name=chunks[key][0], chunk=chunks[key][1], score=score)
            for key, score in ranked
        ]

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
