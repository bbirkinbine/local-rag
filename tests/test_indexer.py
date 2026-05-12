"""Tests for local_rag.indexer — discovery + hashing + orchestration."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path

import pytest

from local_rag.config import Source
from local_rag.indexer import Indexer, _file_sha256, _iter_source_files
from local_rag.store import Store

DIM = 4


# ---------------------------------------------------------------- helpers ---


class FakeEmbedder:
    """Deterministic embedder for tests; records every call."""

    def __init__(self, dim: int = DIM, fail_on_text: str | None = None) -> None:
        self.dim = dim
        self.calls: list[list[str]] = []
        self._fail_on_text = fail_on_text

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self._fail_on_text is not None and any(self._fail_on_text in t for t in texts):
            raise RuntimeError(f"simulated embedder failure on {self._fail_on_text!r}")
        # Deterministic, non-zero vector per text; first slot encodes index.
        return [[1.0 + 0.001 * i] + [0.0] * (self.dim - 1) for i, _ in enumerate(texts)]

    @property
    def total_texts_embedded(self) -> int:
        return sum(len(c) for c in self.calls)


def _make_source(
    name: str,
    path: Path,
    *,
    type_: str = "code",
    ignore: list[str] | None = None,
    respect_gitignore: bool = False,
) -> Source:
    return Source(
        name=name,
        path=path,
        type=type_,  # type: ignore[arg-type]
        ignore=ignore or [],
        respect_gitignore=respect_gitignore,
    )


def _write(p: Path, content: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _paths(it: Iterable[Path]) -> set[str]:
    return {p.name for p in it}


# --------------------------------------------------------------- hashing ---


def test_file_sha256_is_stable(tmp_path: Path) -> None:
    p = _write(tmp_path / "a.txt", "hello world")
    assert _file_sha256(p) == _file_sha256(p)


def test_file_sha256_differs_on_content_change(tmp_path: Path) -> None:
    a = _write(tmp_path / "a.txt", "one")
    b = _write(tmp_path / "b.txt", "two")
    assert _file_sha256(a) != _file_sha256(b)


# ------------------------------------------------------------- discovery ---


def test_discovery_returns_only_allowlisted_extensions(tmp_path: Path) -> None:
    _write(tmp_path / "keep.py", "x = 1\n")
    _write(tmp_path / "keep.md", "# t\n")
    _write(tmp_path / "skip.png", "binary garbage")
    _write(tmp_path / "skip.lock", "lockfile")
    src = _make_source("s", tmp_path)

    found = _paths(_iter_source_files(src, max_bytes=10_000_000))

    assert found == {"keep.py", "keep.md"}


def test_discovery_skips_oversized_files(tmp_path: Path) -> None:
    _write(tmp_path / "small.py", "x\n")
    big = tmp_path / "big.py"
    big.write_text("x" * 5000)
    src = _make_source("s", tmp_path)

    found = _paths(_iter_source_files(src, max_bytes=1000))

    assert found == {"small.py"}


def test_discovery_respects_ignore_globs(tmp_path: Path) -> None:
    _write(tmp_path / "keep.md", "# keep\n")
    _write(tmp_path / ".obsidian" / "config.json", "{}")
    _write(tmp_path / ".obsidian" / "deep" / "more.json", "{}")
    src = _make_source("s", tmp_path, ignore=[".obsidian/**"])

    found = _paths(_iter_source_files(src, max_bytes=10_000_000))

    assert found == {"keep.md"}


def test_discovery_respects_gitignore_when_enabled(tmp_path: Path) -> None:
    """git ls-files filters out anything ignored, including build dirs."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit",
         "--allow-empty", "-m", "init", "-q"],
        cwd=tmp_path, check=True,
    )
    _write(tmp_path / ".gitignore", "build/\nsecret.py\n")
    _write(tmp_path / "keep.py", "x\n")
    _write(tmp_path / "secret.py", "y\n")
    _write(tmp_path / "build" / "out.py", "z\n")
    src = _make_source("s", tmp_path, respect_gitignore=True)

    found = _paths(_iter_source_files(src, max_bytes=10_000_000))

    assert found == {"keep.py"}


def test_discovery_gitignore_falls_back_when_not_a_repo(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "x\n")
    src = _make_source("s", tmp_path, respect_gitignore=True)

    found = _paths(_iter_source_files(src, max_bytes=10_000_000))

    assert "a.py" in found  # still walked, no crash


# --------------------------------------------------------------- indexer ---


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "db", vector_dim=DIM)


@pytest.fixture
def src_dir(tmp_path: Path) -> Path:
    d = tmp_path / "vault"
    d.mkdir()
    return d


def test_indexer_first_run_embeds_everything(store: Store, src_dir: Path) -> None:
    _write(src_dir / "a.md", "# A\n\nbody alpha\n")
    _write(src_dir / "b.py", "x = 1\n")
    src = _make_source("vault", src_dir)
    embedder = FakeEmbedder()
    idx = Indexer(store, embedder)

    result = idx.index_source(src)

    statuses = {fr.path.name: fr.status for fr in result.files}
    assert statuses == {"a.md": "embedded", "b.py": "embedded"}
    assert embedder.total_texts_embedded >= 2
    assert store.chunk_counts()["vault"] >= 2


def test_indexer_second_run_no_changes_skips_embedding(
    store: Store, src_dir: Path
) -> None:
    _write(src_dir / "a.md", "# A\n\nbody\n")
    src = _make_source("vault", src_dir)
    Indexer(store, FakeEmbedder()).index_source(src)

    second = FakeEmbedder()
    result = Indexer(store, second).index_source(src)

    assert all(fr.status == "unchanged" for fr in result.files)
    assert second.total_texts_embedded == 0


def test_indexer_reembeds_changed_files_only(store: Store, src_dir: Path) -> None:
    a = _write(src_dir / "a.md", "# A\n\noriginal\n")
    _write(src_dir / "b.py", "x = 1\n")
    src = _make_source("vault", src_dir)
    Indexer(store, FakeEmbedder()).index_source(src)

    a.write_text("# A\n\nupdated\n")
    second = FakeEmbedder()
    result = Indexer(store, second).index_source(src)

    by_name = {fr.path.name: fr.status for fr in result.files}
    assert by_name == {"a.md": "embedded", "b.py": "unchanged"}
    # Embedder saw exactly a.md's new chunk(s) — nothing from b.py.
    assert second.calls == [["# A\n\nupdated\n"]]


def test_indexer_deletes_files_gone_from_disk(store: Store, src_dir: Path) -> None:
    a = _write(src_dir / "a.md", "# A\n\nx\n")
    _write(src_dir / "b.py", "y\n")
    src = _make_source("vault", src_dir)
    Indexer(store, FakeEmbedder()).index_source(src)

    a.unlink()
    result = Indexer(store, FakeEmbedder()).index_source(src)

    deleted = [fr for fr in result.files if fr.status == "deleted"]
    assert [fr.path.name for fr in deleted] == ["a.md"]
    # b.py still in store; a.md gone.
    remaining_paths = set(store.file_hashes("vault").keys())
    assert all("a.md" not in p for p in remaining_paths)
    assert any(p.endswith("b.py") for p in remaining_paths)


def test_indexer_adds_new_files_on_subsequent_run(
    store: Store, src_dir: Path
) -> None:
    _write(src_dir / "a.md", "# A\n\nx\n")
    src = _make_source("vault", src_dir)
    Indexer(store, FakeEmbedder()).index_source(src)

    _write(src_dir / "new.py", "z = 3\n")
    result = Indexer(store, FakeEmbedder()).index_source(src)

    by_name = {fr.path.name: fr.status for fr in result.files}
    assert by_name == {"a.md": "unchanged", "new.py": "embedded"}


def test_indexer_skips_binary_extensions(store: Store, src_dir: Path) -> None:
    _write(src_dir / "good.md", "# good\n")
    _write(src_dir / "image.png", "PNG\x00binary")
    src = _make_source("vault", src_dir)

    result = Indexer(store, FakeEmbedder()).index_source(src)

    assert [fr.path.name for fr in result.files] == ["good.md"]


def test_indexer_marks_oversize_files(store: Store, src_dir: Path) -> None:
    big = src_dir / "big.md"
    big.write_text("x" * (2_000_000))
    src = _make_source("vault", src_dir)
    idx = Indexer(store, FakeEmbedder(), max_file_bytes=1_048_576)

    result = idx.index_source(src)

    assert [fr.status for fr in result.files] == ["skipped_oversize"]


def test_indexer_oversize_file_does_not_get_orphan_deleted(
    store: Store, src_dir: Path
) -> None:
    """A previously-indexed file that grows past the cap stays in the result as
    oversize, not double-reported as deleted + oversize."""
    p = _write(src_dir / "a.md", "# A\n\nshort\n")
    src = _make_source("vault", src_dir)
    idx = Indexer(store, FakeEmbedder(), max_file_bytes=10_000)
    idx.index_source(src)

    p.write_text("x" * 20_000)  # over the cap
    result = Indexer(store, FakeEmbedder(), max_file_bytes=10_000).index_source(src)

    statuses = [fr.status for fr in result.files]
    assert statuses == ["skipped_oversize"]
    # Not deleted from disk, so it shouldn't appear as 'deleted' anywhere.
    assert "deleted" not in statuses


def test_indexer_empty_file_status_is_empty(store: Store, src_dir: Path) -> None:
    _write(src_dir / "blank.md", "   \n\n")
    src = _make_source("vault", src_dir)

    result = Indexer(store, FakeEmbedder()).index_source(src)

    assert [fr.status for fr in result.files] == ["empty"]


def test_indexer_unreadable_file_is_surfaced(
    store: Store, src_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(src_dir / "good.md", "# good\n")
    bad = _write(src_dir / "bad.md", "# bad\n")
    src = _make_source("vault", src_dir)

    original_read_text = Path.read_text

    def fake_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == bad:
            raise OSError("simulated read failure")
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    result = Indexer(store, FakeEmbedder()).index_source(src)

    by_name = {fr.path.name: fr.status for fr in result.files}
    assert by_name == {"good.md": "embedded", "bad.md": "skipped_unreadable"}


def test_indexer_embedder_error_does_not_stop_other_files(
    store: Store, src_dir: Path
) -> None:
    _write(src_dir / "good.md", "# good\n\nharmless\n")
    _write(src_dir / "bad.md", "# bad\n\nPOISON content\n")
    src = _make_source("vault", src_dir)
    embedder = FakeEmbedder(fail_on_text="POISON")
    idx = Indexer(store, embedder)

    result = idx.index_source(src)

    by_name = {fr.path.name: fr.status for fr in result.files}
    assert by_name == {"good.md": "embedded", "bad.md": "error"}
    bad_file = next(fr for fr in result.files if fr.path.name == "bad.md")
    assert bad_file.error is not None


def test_indexer_result_carries_source_name(store: Store, src_dir: Path) -> None:
    _write(src_dir / "a.md", "# A\n")
    src = _make_source("notes", src_dir)

    result = Indexer(store, FakeEmbedder()).index_source(src)

    assert result.source_name == "notes"


def test_indexer_creates_table_if_missing(store: Store, src_dir: Path) -> None:
    _write(src_dir / "a.md", "# A\n\nbody\n")
    src = _make_source("brandnew", src_dir)
    assert "brandnew" not in store.list_sources()

    Indexer(store, FakeEmbedder()).index_source(src)

    assert "brandnew" in store.list_sources()


# ----------------------------------------------------- store.file_hashes ---


def test_store_file_hashes_empty_table(store: Store) -> None:
    store.ensure_table("vault")
    assert store.file_hashes("vault") == {}


def test_store_file_hashes_reflects_state(store: Store, src_dir: Path) -> None:
    _write(src_dir / "a.md", "# A\n\nbody\n")
    _write(src_dir / "b.py", "x = 1\n")
    src = _make_source("vault", src_dir)
    Indexer(store, FakeEmbedder()).index_source(src)

    hashes = store.file_hashes("vault")

    assert len(hashes) == 2
    paths = set(hashes.keys())
    assert any(p.endswith("a.md") for p in paths)
    assert any(p.endswith("b.py") for p in paths)
