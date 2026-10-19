"""Build a LanceDB table with the pre-slice-12 chunk schema, for migration tests.

The column list is written out here on purpose rather than imported from the
package: it must keep describing the *old* layout after the package moves on.
No binary fixture is checked in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa


def legacy_schema(dim: int) -> pa.Schema:
    """The nine-column chunk schema shipped before frontmatter metadata."""
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
            pa.field("vector", pa.list_(pa.float32(), dim), nullable=False),
        ]
    )


def legacy_row(
    *,
    path: str,
    idx: int,
    text: str,
    dim: int,
    file_hash: str = "h0",
    heading: str = "",
) -> dict[str, Any]:  # `Any`: heterogeneous LanceDB row values.
    """One row in the legacy layout, with a unit vector on the first axis."""
    return {
        "id": f"{path}::{idx}",
        "source_path": path,
        "file_hash": file_hash,
        "chunk_index": idx,
        "char_start": 0,
        "char_end": len(text),
        "heading_path": heading,
        "text": text,
        "vector": [1.0] + [0.0] * (dim - 1),
    }


def make_legacy_table(
    db_path: Path,
    name: str,
    dim: int,
    rows: list[dict[str, Any]],  # `Any`: heterogeneous LanceDB row values.
) -> None:
    """Create table ``name`` under ``db_path`` with the legacy schema and ``rows``."""
    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))
    tbl = db.create_table(name, schema=legacy_schema(dim))
    if rows:
        tbl.add(rows)
    tbl.create_fts_index("text", use_tantivy=False, replace=True)
