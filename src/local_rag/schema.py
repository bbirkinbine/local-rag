"""The chunk table's row schema and the ``Chunk`` <-> row mapping.

Tables are created with :func:`chunk_schema` and never altered in place. A
table built by older code lacks columns the code now writes; such a table is
*stale* (:func:`missing_columns`). Indexing refuses stale tables until
``local-rag index --force`` recreates them, while reads stay tolerant:
:func:`row_to_chunk` fills absent columns with ``Chunk`` defaults, so search
keeps working against a stale table.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pyarrow as pa

from local_rag.models import Chunk


class StaleSchemaError(Exception):
    """A table predates the current schema; ``local-rag index --force`` rebuilds it."""


def chunk_schema(vector_dim: int) -> pa.Schema:
    """The row schema for every source table."""
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
            pa.field("vector", pa.list_(pa.float32(), vector_dim), nullable=False),
            pa.field("note_date", pa.date32(), nullable=True),
            pa.field("status", pa.string(), nullable=True),
            pa.field("tags", pa.list_(pa.string()), nullable=False),
            pa.field("kind", pa.string(), nullable=False),
        ]
    )


# The vector width doesn't affect column names, so any value works here.
EXPECTED_COLUMNS: frozenset[str] = frozenset(chunk_schema(1).names)


def missing_columns(actual: pa.Schema) -> list[str]:
    """Columns the current schema expects that ``actual`` lacks, sorted."""
    return sorted(EXPECTED_COLUMNS - set(actual.names))


# `Any`: a row holds mixed column types (str, int, list, date, None), the shape
# LanceDB's untyped API reads and writes.
def chunk_to_row(c: Chunk, vector_dim: int) -> dict[str, Any]:
    """Map a chunk to a table row.

    Raises:
        ValueError: if the chunk's vector length does not match ``vector_dim``.
    """
    if len(c.vector) != vector_dim:
        raise ValueError(
            f"chunk {c.id}: vector dim mismatch (expected {vector_dim}, got {len(c.vector)})"
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
        "note_date": c.note_date,
        "status": c.status,
        "tags": list(c.tags),
        "kind": c.kind,
    }


# `Any`: same row shape as `chunk_to_row`.
def row_to_chunk(row: dict[str, Any]) -> Chunk:
    """Map a table row back to a chunk; columns a stale table lacks get defaults."""
    note_date = row.get("note_date")
    status = row.get("status")
    return Chunk(
        source_path=str(row["source_path"]),
        file_hash=str(row["file_hash"]),
        chunk_index=int(row["chunk_index"]),
        char_start=int(row["char_start"]),
        char_end=int(row["char_end"]),
        heading_path=str(row["heading_path"]),
        text=str(row["text"]),
        vector=[float(x) for x in row["vector"]],
        note_date=note_date if isinstance(note_date, date) else None,
        status=str(status) if status is not None else None,
        tags=tuple(str(t) for t in row.get("tags") or ()),
        kind=str(row.get("kind") or "text"),
    )
