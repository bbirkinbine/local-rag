"""Tests for local_rag.evals — the golden-query retrieval eval harness."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_rag import cli
from local_rag.evals import (
    EvalError,
    GoldenQuery,
    evaluate,
    load_golden_queries,
    path_matches,
)
from local_rag.indexer import Embedder
from local_rag.models import Chunk
from local_rag.store import Store

DIM = 4


# ------------------------------------------------------------- test doubles


class FakeEmbedder:
    """Embedder double: deterministic near-identical vectors, so ranking in
    end-to-end tests is driven by the FTS half of the hybrid search."""

    def __init__(self, dim: int = DIM) -> None:
        self.dim = dim
        self.health_check_called = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0 + 0.01 * i] + [0.0] * (self.dim - 1) for i, _ in enumerate(texts)]

    def health_check(self) -> None:
        self.health_check_called = True


class FixedVectorEmbedder:
    """Embedder double returning one fixed vector for every input."""

    def __init__(self, vector: list[float]) -> None:
        self.vector = vector

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(self.vector) for _ in texts]

    def health_check(self) -> None:
        pass


def _chunk(path: str, index: int, text: str, vector: list[float]) -> Chunk:
    return Chunk(
        source_path=path,
        file_hash="h",
        chunk_index=index,
        char_start=0,
        char_end=len(text),
        heading_path="",
        text=text,
        vector=vector,
    )


def _write_config(tmp_path: Path, sources: list[tuple[str, Path, str]]) -> Path:
    cfg = tmp_path / "config.toml"
    lines = [
        f'db_path = "{tmp_path / "db"}"\n',
        "\n[embedding]\n",
        'provider = "ollama"\n',
        'model = "bge-m3"\n',
        'url = "http://localhost:11434"\n',
        f"dim = {DIM}\n\n",
    ]
    for name, path, typ in sources:
        lines += [
            "[[sources]]\n",
            f'name = "{name}"\n',
            f'path = "{path}"\n',
            f'type = "{typ}"\n\n',
        ]
    cfg.write_text("".join(lines))
    return cfg


def _make_source_dir(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    d = tmp_path / name
    d.mkdir()
    for rel, content in files.items():
        f = d / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    return d


@pytest.fixture
def patch_embedder(monkeypatch: pytest.MonkeyPatch) -> FakeEmbedder:
    fake = FakeEmbedder()

    def factory(*args: object, **kwargs: object) -> Embedder:
        return fake  # type: ignore[return-value]

    monkeypatch.setattr(cli, "_build_embedder", factory)
    return fake


# ------------------------------------------------------- load_golden_queries


def test_load_golden_queries_parses_toml(tmp_path: Path) -> None:
    golden = tmp_path / "golden.toml"
    golden.write_text(
        '[[queries]]\nquery = "alpha"\nexpected_paths = ["notes/a.md"]\n\n'
        '[[queries]]\nquery = "bravo"\nexpected_paths = ["notes/b.md", "notes/c.md"]\n'
    )

    queries = load_golden_queries(golden)

    assert queries == [
        GoldenQuery(query="alpha", expected_paths=("notes/a.md",)),
        GoldenQuery(query="bravo", expected_paths=("notes/b.md", "notes/c.md")),
    ]


def test_load_golden_queries_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(EvalError, match="golden"):
        load_golden_queries(tmp_path / "nope.toml")


def test_load_golden_queries_rejects_missing_query_field(tmp_path: Path) -> None:
    golden = tmp_path / "golden.toml"
    golden.write_text('[[queries]]\nexpected_paths = ["a.md"]\n')

    with pytest.raises(EvalError, match="query"):
        load_golden_queries(golden)


def test_load_golden_queries_rejects_empty_expected_paths(tmp_path: Path) -> None:
    golden = tmp_path / "golden.toml"
    golden.write_text('[[queries]]\nquery = "q"\nexpected_paths = []\n')

    with pytest.raises(EvalError, match="expected_paths"):
        load_golden_queries(golden)


def test_load_golden_queries_rejects_no_queries(tmp_path: Path) -> None:
    golden = tmp_path / "golden.toml"
    golden.write_text("# empty\n")

    with pytest.raises(EvalError, match="no queries"):
        load_golden_queries(golden)


# --------------------------------------------------------------- path_matches


def test_path_matches_exact() -> None:
    assert path_matches("notes/a.md", "notes/a.md") is True


def test_path_matches_suffix_on_path_boundary() -> None:
    assert path_matches("vault/notes/a.md", "notes/a.md") is True


def test_path_matches_rejects_partial_filename() -> None:
    # "b.md" must not match "ab.md" even though it's a string suffix.
    assert path_matches("notes/ab.md", "b.md") is False


def test_path_matches_rejects_unrelated_path() -> None:
    assert path_matches("notes/a.md", "other/b.md") is False


# ------------------------------------------------------------------ evaluate


def test_evaluate_perfect_recall_and_mrr(tmp_path: Path) -> None:
    store = Store(tmp_path / "db", vector_dim=DIM)
    store.ensure_table("vault")
    store.upsert_chunks(
        "vault",
        [
            _chunk("notes/a.md", 0, "alpha content", [1.0, 0.0, 0.0, 0.0]),
            _chunk("notes/b.md", 0, "bravo content", [0.0, 1.0, 0.0, 0.0]),
        ],
    )
    embedder = FixedVectorEmbedder([1.0, 0.0, 0.0, 0.0])
    queries = [GoldenQuery(query="alpha content", expected_paths=("notes/a.md",))]

    report = evaluate(store, embedder, ["vault"], queries, k=5)

    assert report.recall == 1.0
    assert report.mrr == 1.0
    assert report.results[0].best_rank == 1


def test_evaluate_miss_scores_zero(tmp_path: Path) -> None:
    store = Store(tmp_path / "db", vector_dim=DIM)
    store.ensure_table("vault")
    store.upsert_chunks(
        "vault",
        [_chunk("notes/a.md", 0, "alpha content", [1.0, 0.0, 0.0, 0.0])],
    )
    embedder = FixedVectorEmbedder([1.0, 0.0, 0.0, 0.0])
    queries = [GoldenQuery(query="anything", expected_paths=("notes/missing.md",))]

    report = evaluate(store, embedder, ["vault"], queries, k=5)

    assert report.recall == 0.0
    assert report.mrr == 0.0
    assert report.results[0].best_rank is None


def test_evaluate_ranks_files_not_chunks(tmp_path: Path) -> None:
    """Two chunks of file A outrank file B's chunk; B's file rank must be 2."""
    store = Store(tmp_path / "db", vector_dim=DIM)
    store.ensure_table("vault")
    store.upsert_chunks(
        "vault",
        [
            _chunk("notes/a.md", 0, "aardvark one", [1.0, 0.0, 0.0, 0.0]),
            _chunk("notes/a.md", 1, "aardvark two", [0.9, 0.1, 0.0, 0.0]),
            _chunk("notes/b.md", 0, "bumblebee", [0.0, 0.0, 1.0, 0.0]),
        ],
    )
    # Query vector sits on file A's chunks; no FTS overlap with any text.
    embedder = FixedVectorEmbedder([1.0, 0.0, 0.0, 0.0])
    queries = [GoldenQuery(query="zzz nomatch", expected_paths=("notes/b.md",))]

    report = evaluate(store, embedder, ["vault"], queries, k=5)

    assert report.results[0].best_rank == 2
    assert report.mrr == 0.5


def test_evaluate_recall_averages_over_queries(tmp_path: Path) -> None:
    store = Store(tmp_path / "db", vector_dim=DIM)
    store.ensure_table("vault")
    store.upsert_chunks(
        "vault",
        [_chunk("notes/a.md", 0, "alpha content", [1.0, 0.0, 0.0, 0.0])],
    )
    embedder = FixedVectorEmbedder([1.0, 0.0, 0.0, 0.0])
    queries = [
        GoldenQuery(query="alpha content", expected_paths=("notes/a.md",)),
        GoldenQuery(query="anything", expected_paths=("notes/missing.md",)),
    ]

    report = evaluate(store, embedder, ["vault"], queries, k=5)

    assert report.recall == 0.5
    assert report.mrr == 0.5


def test_evaluate_rejects_empty_query_list(tmp_path: Path) -> None:
    store = Store(tmp_path / "db", vector_dim=DIM)
    embedder = FixedVectorEmbedder([1.0, 0.0, 0.0, 0.0])

    with pytest.raises(EvalError, match="no queries"):
        evaluate(store, embedder, ["vault"], [], k=5)


# ----------------------------------------------------------------- CLI: eval


def test_eval_cmd_reports_metrics(
    tmp_path: Path, patch_embedder: FakeEmbedder, capsys: pytest.CaptureFixture[str]
) -> None:
    src_dir = _make_source_dir(
        tmp_path,
        "vault",
        {"a.md": "# A\n\nthe quick brown fox\n", "b.md": "# B\n\nslow green turtle\n"},
    )
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])
    golden = tmp_path / "golden.toml"
    golden.write_text('[[queries]]\nquery = "quick brown fox"\nexpected_paths = ["a.md"]\n')

    cli.main(["--config", str(cfg), "index"])
    capsys.readouterr()

    rc = cli.main(["--config", str(cfg), "eval", "--golden", str(golden)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "recall@5" in out
    assert "mrr" in out
    assert "1.000" in out  # the single golden query hits at rank 1


def test_eval_cmd_missing_golden_exits_two(
    tmp_path: Path, patch_embedder: FakeEmbedder, capsys: pytest.CaptureFixture[str]
) -> None:
    src_dir = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n\nbody\n"})
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])
    cli.main(["--config", str(cfg), "index"])
    capsys.readouterr()

    rc = cli.main(["--config", str(cfg), "eval", "--golden", str(tmp_path / "nope.toml")])

    assert rc == 2
    err = capsys.readouterr().err
    assert "golden" in err.lower()


def test_eval_cmd_runs_health_check(tmp_path: Path, patch_embedder: FakeEmbedder) -> None:
    src_dir = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n\nbody\n"})
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])
    golden = tmp_path / "golden.toml"
    golden.write_text('[[queries]]\nquery = "body"\nexpected_paths = ["a.md"]\n')
    cli.main(["--config", str(cfg), "index"])

    cli.main(["--config", str(cfg), "eval", "--golden", str(golden)])

    assert patch_embedder.health_check_called is True


# --------------------------------------------------------- negative queries


def test_load_golden_queries_parses_negative_entry(tmp_path: Path) -> None:
    golden = tmp_path / "golden.toml"
    golden.write_text('[[queries]]\nquery = "nonsense"\nexpect_max_cosine = 0.5\n')

    queries = load_golden_queries(golden)

    assert queries[0].expected_paths == ()
    assert queries[0].expect_max_cosine == 0.5
    assert queries[0].is_negative


def test_load_golden_queries_rejects_both_expectation_kinds(tmp_path: Path) -> None:
    golden = tmp_path / "golden.toml"
    golden.write_text(
        '[[queries]]\nquery = "q"\nexpected_paths = ["a.md"]\nexpect_max_cosine = 0.5\n'
    )

    with pytest.raises(EvalError, match="not both"):
        load_golden_queries(golden)


def test_load_golden_queries_rejects_out_of_range_threshold(tmp_path: Path) -> None:
    golden = tmp_path / "golden.toml"
    golden.write_text('[[queries]]\nquery = "q"\nexpect_max_cosine = 1.5\n')

    with pytest.raises(EvalError, match="expect_max_cosine"):
        load_golden_queries(golden)


def test_evaluate_negative_query_passes_when_all_hits_weak(tmp_path: Path) -> None:
    store = Store(tmp_path / "db", vector_dim=DIM)
    store.ensure_table("vault")
    store.upsert_chunks(
        "vault",
        [_chunk("notes/a.md", 0, "some content", [1.0, 0.0, 0.0, 0.0])],
    )
    # Query vector orthogonal to everything indexed -> cosine ~0.
    embedder = FixedVectorEmbedder([0.0, 1.0, 0.0, 0.0])
    queries = [GoldenQuery(query="zzz", expected_paths=(), expect_max_cosine=0.5)]

    report = evaluate(store, embedder, ["vault"], queries, k=5)

    result = report.results[0]
    assert result.passed is True
    assert result.max_cosine == pytest.approx(0.0, abs=1e-4)


def test_evaluate_negative_query_fails_on_strong_hit(tmp_path: Path) -> None:
    store = Store(tmp_path / "db", vector_dim=DIM)
    store.ensure_table("vault")
    store.upsert_chunks(
        "vault",
        [_chunk("notes/a.md", 0, "some content", [1.0, 0.0, 0.0, 0.0])],
    )
    embedder = FixedVectorEmbedder([1.0, 0.0, 0.0, 0.0])  # cosine 1.0 hit exists
    queries = [GoldenQuery(query="zzz", expected_paths=(), expect_max_cosine=0.5)]

    report = evaluate(store, embedder, ["vault"], queries, k=5)

    result = report.results[0]
    assert result.passed is False
    assert result.max_cosine == pytest.approx(1.0, abs=1e-4)


def test_negative_queries_do_not_affect_recall_and_mrr(tmp_path: Path) -> None:
    store = Store(tmp_path / "db", vector_dim=DIM)
    store.ensure_table("vault")
    store.upsert_chunks(
        "vault",
        [_chunk("notes/a.md", 0, "alpha content", [1.0, 0.0, 0.0, 0.0])],
    )
    embedder = FixedVectorEmbedder([1.0, 0.0, 0.0, 0.0])
    queries = [
        GoldenQuery(query="alpha content", expected_paths=("notes/a.md",)),
        GoldenQuery(query="zzz", expected_paths=(), expect_max_cosine=0.5),  # fails
    ]

    report = evaluate(store, embedder, ["vault"], queries, k=5)

    # recall/mrr computed over the one positive query only.
    assert report.recall == 1.0
    assert report.mrr == 1.0
    assert report.negatives_total == 1
    assert report.negatives_passed == 0


def test_eval_cmd_reports_negative_summary(
    tmp_path: Path, patch_embedder: FakeEmbedder, capsys: pytest.CaptureFixture[str]
) -> None:
    src_dir = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n\nquick brown fox\n"})
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])
    golden = tmp_path / "golden.toml"
    golden.write_text(
        '[[queries]]\nquery = "quick brown fox"\nexpected_paths = ["a.md"]\n\n'
        '[[queries]]\nquery = "unrelated nonsense"\nexpect_max_cosine = 0.5\n'
    )
    cli.main(["--config", str(cfg), "index"])
    capsys.readouterr()

    rc = cli.main(["--config", str(cfg), "eval", "--golden", str(golden)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "negatives=" in out
