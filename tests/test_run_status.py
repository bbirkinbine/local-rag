"""Tests for local_rag.run_status — the per-source freshness record."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from local_rag.indexer import FileResult, IndexResult
from local_rag.run_status import RunStatusLog

T0 = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 19, 12, 30, tzinfo=UTC)


def _result(*files: FileResult, name: str = "vault") -> IndexResult:
    return IndexResult(source_name=name, files=list(files))


def _changed() -> FileResult:
    return FileResult(path=Path("/v/a.md"), status="embedded", chunk_count=2)


def _unchanged() -> FileResult:
    return FileResult(path=Path("/v/a.md"), status="unchanged")


@pytest.fixture
def status_log(tmp_path: Path) -> RunStatusLog:
    return RunStatusLog(tmp_path / "db")


def test_read_without_file_is_empty(status_log: RunStatusLog) -> None:
    assert status_log.read() == {}


def test_record_run_sets_last_run(status_log: RunStatusLog) -> None:
    status_log.record_run(_result(_unchanged()), embedding_model="bge-m3", now=T0)

    assert status_log.read()["vault"]["last_run"] == T0.isoformat()


def test_record_run_sets_embedding_model(status_log: RunStatusLog) -> None:
    status_log.record_run(_result(_unchanged()), embedding_model="bge-m3", now=T0)

    assert status_log.read()["vault"]["embedding_model"] == "bge-m3"


def test_record_run_with_changes_sets_last_change(status_log: RunStatusLog) -> None:
    status_log.record_run(_result(_changed()), embedding_model="bge-m3", now=T0)

    assert status_log.read()["vault"]["last_change"] == T0.isoformat()


def test_record_run_without_changes_keeps_prior_last_change(status_log: RunStatusLog) -> None:
    status_log.record_run(_result(_changed()), embedding_model="bge-m3", now=T0)
    status_log.record_run(_result(_unchanged()), embedding_model="bge-m3", now=T1)

    assert status_log.read()["vault"]["last_change"] == T0.isoformat()


def test_first_run_without_changes_has_no_last_change(status_log: RunStatusLog) -> None:
    status_log.record_run(_result(_unchanged()), embedding_model="bge-m3", now=T0)

    assert status_log.read()["vault"]["last_change"] is None


def test_failures_are_grouped_by_error_message(status_log: RunStatusLog) -> None:
    errs = [
        FileResult(path=Path(f"/v/{i}.md"), status="error", error="ollama down") for i in range(3)
    ]
    status_log.record_run(_result(*errs), embedding_model="bge-m3", now=T0)

    assert status_log.read()["vault"]["failures"] == [
        {"error": "ollama down", "count": 3, "example_path": "/v/0.md"}
    ]


def test_unreadable_files_count_as_failures(status_log: RunStatusLog) -> None:
    bad = FileResult(path=Path("/v/x.md"), status="skipped_unreadable", error="EACCES")
    status_log.record_run(_result(bad), embedding_model="bge-m3", now=T0)

    assert status_log.read()["vault"]["failures"][0]["error"] == "EACCES"


def test_oversize_files_are_not_failures(status_log: RunStatusLog) -> None:
    big = FileResult(path=Path("/v/big.md"), status="skipped_oversize")
    status_log.record_run(_result(big), embedding_model="bge-m3", now=T0)

    assert status_log.read()["vault"]["failures"] == []


def test_record_error_sets_last_error(status_log: RunStatusLog) -> None:
    status_log.record_error(["vault"], "embedder unreachable", now=T0)

    assert status_log.read()["vault"]["last_error"] == "embedder unreachable"


def test_record_error_keeps_last_successful_run(status_log: RunStatusLog) -> None:
    status_log.record_run(_result(_unchanged()), embedding_model="bge-m3", now=T0)
    status_log.record_error(["vault"], "embedder unreachable", now=T1)

    entry = status_log.read()["vault"]
    assert (entry["last_run"], entry["last_attempt"]) == (T0.isoformat(), T1.isoformat())


def test_successful_run_clears_last_error(status_log: RunStatusLog) -> None:
    status_log.record_error(["vault"], "embedder unreachable", now=T0)
    status_log.record_run(_result(_unchanged()), embedding_model="bge-m3", now=T1)

    assert status_log.read()["vault"]["last_error"] is None


def test_record_run_leaves_other_sources_alone(status_log: RunStatusLog) -> None:
    status_log.record_run(_result(_unchanged(), name="code"), embedding_model="bge-m3", now=T0)
    status_log.record_run(_result(_unchanged()), embedding_model="bge-m3", now=T1)

    assert status_log.read()["code"]["last_run"] == T0.isoformat()


def test_corrupt_file_reads_as_empty(status_log: RunStatusLog) -> None:
    status_log.path.parent.mkdir(parents=True, exist_ok=True)
    status_log.path.write_text("{not json")

    assert status_log.read() == {}


def test_entry_missing_fields_is_dropped(status_log: RunStatusLog) -> None:
    status_log.path.parent.mkdir(parents=True, exist_ok=True)
    status_log.path.write_text('{"vault": {"last_run": "2026-10-19T12:00:00+00:00"}}')

    assert status_log.read() == {}
