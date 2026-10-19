"""Per-source freshness record: when each source was last indexed, and how.

Kept separate from the store's run log (``.run_log.json``) on purpose. That
log records only runs that changed a table, because it bounds version
pruning; recording no-op runs there would shrink the undo window. Freshness
needs the opposite: every completed run, changed or not, plus the runs that
died before indexing anything (Ollama down at startup) — so a calling agent
can tell "the index is stale" from "the note doesn't exist".

Written by ``local-rag index``; read by the MCP ``index_status`` tool.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, cast

import structlog

from local_rag.indexer import IndexResult

log = structlog.get_logger()

# Filename beside the LanceDB tables, next to the store's ``.run_log.json``.
_STATUS_FILE_NAME = ".index_status.json"

# Statuses that changed the table — the same set that triggers Store.optimize.
_CHANGED = frozenset({"embedded", "deleted", "empty"})

# Statuses that mean a file should have been indexed and wasn't. Oversize
# files are a policy skip, not a failure.
_FAILED = frozenset({"error", "skipped_unreadable"})


class FailureGroup(TypedDict):
    """Files that failed with one error message. Grouped so a run where
    every file hit the same error stays one entry, whatever the corpus size."""

    error: str
    count: int
    example_path: str


class SourceRunStatus(TypedDict):
    last_attempt: str
    last_run: str | None
    last_change: str | None
    last_error: str | None
    embedding_model: str | None
    failures: list[FailureGroup]


_FIELDS = frozenset(SourceRunStatus.__annotations__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RunStatusLog:
    """Reads and writes the freshness record for one LanceDB directory."""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path / _STATUS_FILE_NAME

    @property
    def path(self) -> Path:
        """Where the record lives."""
        return self._path

    def read(self) -> dict[str, SourceRunStatus]:
        """Return ``{source_name: status}``, or ``{}`` if absent or unreadable.

        A damaged record must never break indexing or ``index_status``; it
        reads as "no history", which reports every source as never indexed.
        """
        try:
            raw = json.loads(self._path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as e:
            log.warning("run_status.unreadable", path=str(self._path), error=str(e))
            return {}
        if not isinstance(raw, dict):
            log.warning("run_status.malformed", path=str(self._path))
            return {}
        # Drop entries missing a field (hand-edited, or an older shape) rather
        # than let a KeyError surface in `index_status`.
        return {
            k: cast(SourceRunStatus, v)
            for k, v in raw.items()
            if isinstance(v, dict) and v.keys() >= _FIELDS
        }

    def record_run(
        self,
        result: IndexResult,
        *,
        embedding_model: str,
        now: datetime | None = None,
    ) -> None:
        """Record a completed indexing run for ``result.source_name``.

        Args:
            result: The run's per-file outcomes.
            embedding_model: Model the run embedded with.
            now: Completion time; defaults to the current UTC time.
        """
        stamp = (now or _utc_now()).isoformat()
        data = self.read()
        prior = data.get(result.source_name)
        changed = any(f.status in _CHANGED for f in result.files)
        data[result.source_name] = SourceRunStatus(
            last_attempt=stamp,
            last_run=stamp,
            last_change=stamp if changed else (prior["last_change"] if prior else None),
            last_error=None,
            embedding_model=embedding_model,
            failures=_group_failures(result),
        )
        self._write(data)

    def record_error(
        self, source_names: list[str], error: str, *, now: datetime | None = None
    ) -> None:
        """Record a run that failed before indexing ``source_names``.

        Keeps each source's last successful run, so the record shows both
        how stale the index is and why.

        Args:
            source_names: Sources the run was meant to index.
            error: What stopped it.
            now: Failure time; defaults to the current UTC time.
        """
        stamp = (now or _utc_now()).isoformat()
        data = self.read()
        for name in source_names:
            prior = data.get(name)
            data[name] = SourceRunStatus(
                last_attempt=stamp,
                last_run=prior["last_run"] if prior else None,
                last_change=prior["last_change"] if prior else None,
                last_error=error,
                embedding_model=prior["embedding_model"] if prior else None,
                failures=prior["failures"] if prior else [],
            )
        self._write(data)

    def _write(self, data: dict[str, SourceRunStatus]) -> None:
        """Replace the record atomically; a write failure is not fatal."""
        tmp = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(self._path)
        except OSError as e:
            log.warning("run_status.unwritable", path=str(self._path), error=str(e))


def _group_failures(result: IndexResult) -> list[FailureGroup]:
    """Group failed files by error message, in first-seen order."""
    groups: dict[str, FailureGroup] = {}
    for f in result.files:
        if f.status not in _FAILED:
            continue
        msg = f.error or f.status
        if msg in groups:
            groups[msg]["count"] += 1
        else:
            groups[msg] = FailureGroup(error=msg, count=1, example_path=str(f.path))
    return list(groups.values())
