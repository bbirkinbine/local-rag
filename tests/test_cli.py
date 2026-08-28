"""Tests for local_rag.cli — main entry point + subcommands."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_rag import cli
from local_rag.embedder import EmbedderError
from local_rag.indexer import Embedder

DIM = 4


class FakeEmbedder:
    """Test double matching the Embedder protocol. Records embed() calls."""

    def __init__(self, dim: int = DIM) -> None:
        self.dim = dim
        self.calls: list[list[str]] = []
        self.health_check_called = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[1.0 + 0.01 * i] + [0.0] * (self.dim - 1) for i, _ in enumerate(texts)]

    def health_check(self) -> None:
        self.health_check_called = True

    def warm_up(self) -> None:  # not used by CLI but matches the real shape
        pass


def _write_config(
    tmp_path: Path,
    sources: list[tuple[str, Path, str]],
    dim: int = DIM,
) -> Path:
    """Build a minimal local-rag TOML config at tmp_path/config.toml."""
    cfg = tmp_path / "config.toml"
    db = tmp_path / "db"
    lines = [
        f'db_path = "{db}"\n',
        "\n",
        "[embedding]\n",
        'provider = "ollama"\n',
        'model = "bge-m3"\n',
        'url = "http://localhost:11434"\n',
        f"dim = {dim}\n",
        "\n",
    ]
    for name, path, typ in sources:
        lines.append("[[sources]]\n")
        lines.append(f'name = "{name}"\n')
        lines.append(f'path = "{path}"\n')
        lines.append(f'type = "{typ}"\n')
        lines.append("\n")
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


# -------------------------------------------------------------------- list ---


def test_list_with_no_indexed_sources_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src_dir = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n"})
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])

    rc = cli.main(["--config", str(cfg), "list"])

    assert rc == 0
    out = capsys.readouterr().out
    # No indexing happened yet; expect strictly empty stdout.
    assert out.strip() == ""


def test_list_after_indexing_shows_counts(
    tmp_path: Path, patch_embedder: FakeEmbedder, capsys: pytest.CaptureFixture[str]
) -> None:
    src_dir = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n\nbody\n"})
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])

    cli.main(["--config", str(cfg), "index"])
    capsys.readouterr()  # clear

    rc = cli.main(["--config", str(cfg), "list"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "vault" in out
    # At least one chunk indexed.
    assert any(token.isdigit() and int(token) > 0 for token in out.split())


def test_list_works_without_embedder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """list must not require Ollama — embedder factory is not called."""
    src_dir = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n"})
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])

    called = {"factory": False}

    def boom(*args: object, **kwargs: object) -> Embedder:
        called["factory"] = True
        raise AssertionError("list should not build an embedder")

    monkeypatch.setattr(cli, "_build_embedder", boom)

    rc = cli.main(["--config", str(cfg), "list"])

    assert rc == 0
    assert called["factory"] is False


# ------------------------------------------------------------------- index ---


def test_index_all_sources_default(
    tmp_path: Path, patch_embedder: FakeEmbedder, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n\nbody\n"})
    code = _make_source_dir(tmp_path, "code", {"b.py": "x = 1\n"})
    cfg = _write_config(tmp_path, [("vault", vault, "markdown"), ("code", code, "code")])

    rc = cli.main(["--config", str(cfg), "index"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "vault" in out
    assert "code" in out


def test_index_single_source_skips_others(
    tmp_path: Path, patch_embedder: FakeEmbedder, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n\nbody\n"})
    code = _make_source_dir(tmp_path, "code", {"b.py": "x = 1\n"})
    cfg = _write_config(tmp_path, [("vault", vault, "markdown"), ("code", code, "code")])

    rc = cli.main(["--config", str(cfg), "index", "vault"])

    assert rc == 0
    # Only vault chunks should have been embedded.
    embedded_texts = [t for call in patch_embedder.calls for t in call]
    assert any("# A" in t for t in embedded_texts)
    assert not any("x = 1" in t for t in embedded_texts)


def test_index_unknown_source_exits_two(
    tmp_path: Path, patch_embedder: FakeEmbedder, capsys: pytest.CaptureFixture[str]
) -> None:
    src_dir = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n"})
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])

    rc = cli.main(["--config", str(cfg), "index", "nope"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "vault" in err  # lists valid sources


def test_index_force_reembeds_unchanged_files(
    tmp_path: Path, patch_embedder: FakeEmbedder, capsys: pytest.CaptureFixture[str]
) -> None:
    """--force ignores stored file hashes — the chunker-change migration path."""
    src_dir = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n\nbody\n"})
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])

    cli.main(["--config", str(cfg), "index"])
    calls_after_first = len(patch_embedder.calls)
    cli.main(["--config", str(cfg), "index"])
    # Incremental: unchanged file, no new embed calls.
    assert len(patch_embedder.calls) == calls_after_first

    rc = cli.main(["--config", str(cfg), "index", "--force"])

    assert rc == 0
    assert len(patch_embedder.calls) > calls_after_first


def test_index_runs_health_check(tmp_path: Path, patch_embedder: FakeEmbedder) -> None:
    src_dir = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n"})
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])

    cli.main(["--config", str(cfg), "index"])

    assert patch_embedder.health_check_called is True


# ------------------------------------------------------------------ search ---


def test_search_returns_hits_after_indexing(
    tmp_path: Path, patch_embedder: FakeEmbedder, capsys: pytest.CaptureFixture[str]
) -> None:
    src_dir = _make_source_dir(
        tmp_path,
        "vault",
        {"a.md": "# Heading\n\nthe quick brown fox\n"},
    )
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])

    cli.main(["--config", str(cfg), "index"])
    capsys.readouterr()  # clear index output

    rc = cli.main(["--config", str(cfg), "search", "quick"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "score=" in out
    assert "vault" in out
    assert "the quick brown fox" in out


def test_search_no_results_message(
    tmp_path: Path, patch_embedder: FakeEmbedder, capsys: pytest.CaptureFixture[str]
) -> None:
    # An empty source: index runs but produces no chunks.
    src_dir = _make_source_dir(tmp_path, "vault", {})
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])
    cli.main(["--config", str(cfg), "index"])
    capsys.readouterr()

    rc = cli.main(["--config", str(cfg), "search", "anything"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "no results" in out


def test_search_honors_k_flag(
    tmp_path: Path, patch_embedder: FakeEmbedder, capsys: pytest.CaptureFixture[str]
) -> None:
    src_dir = _make_source_dir(
        tmp_path,
        "vault",
        {"a.md": "# A\n\nalpha\n\n# B\n\nbravo\n\n# C\n\ncharlie\n"},
    )
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])
    cli.main(["--config", str(cfg), "index"])
    capsys.readouterr()

    cli.main(["--config", str(cfg), "search", "alpha", "-k", "1"])

    out = capsys.readouterr().out
    assert out.count("score=") == 1


# ---------------------------------------------------------- config errors ---


def test_missing_config_file_exits_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["--config", str(tmp_path / "nope.toml"), "list"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "config" in err.lower()


# -------------------------------------------------------- exit-code-3 / env ---


def test_health_check_failure_exits_three(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src_dir = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n"})
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])

    class DeadEmbedder(FakeEmbedder):
        def health_check(self) -> None:
            raise EmbedderError("ollama not responding")

    def factory(*args: object, **kwargs: object) -> Embedder:
        return DeadEmbedder()  # type: ignore[return-value]

    monkeypatch.setattr(cli, "_build_embedder", factory)

    rc = cli.main(["--config", str(cfg), "index"])

    assert rc == 3
    err = capsys.readouterr().err
    assert "ollama" in err.lower()


def test_local_rag_config_env_var_is_honored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src_dir = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n"})
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])
    monkeypatch.setenv("LOCAL_RAG_CONFIG", str(cfg))

    rc = cli.main(["list"])  # no --config flag

    assert rc == 0


# -------------------------------------------------- search --sources flag ---


def test_search_unknown_source_exits_two(
    tmp_path: Path, patch_embedder: FakeEmbedder, capsys: pytest.CaptureFixture[str]
) -> None:
    src_dir = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n\nbody\n"})
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])
    cli.main(["--config", str(cfg), "index"])
    capsys.readouterr()

    rc = cli.main(["--config", str(cfg), "search", "anything", "--sources", "nope"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "vault" in err  # surfaces valid sources


def test_mcp_dispatches_to_run_stdio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patch_embedder: FakeEmbedder,
) -> None:
    src_dir = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n"})
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])

    called = {"hit": False}

    def fake_run_stdio(*args: object, **kwargs: object) -> None:
        called["hit"] = True

    monkeypatch.setattr(cli, "run_stdio", fake_run_stdio)

    rc = cli.main(["--config", str(cfg), "mcp"])

    assert rc == 0
    assert called["hit"] is True


def test_mcp_rejects_removed_http_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patch_embedder: FakeEmbedder,
) -> None:
    """The HTTP/HTTPS transport was removed; its flags must fail to parse.

    argparse exits with SystemExit(2) on an unrecognized argument. The
    monkeypatches are hang guards only: if a flag were wrongly accepted,
    main() would fall through to a real (blocking) server. run_http is
    patched with raising=False so the guard is a no-op once it's gone.
    """
    src_dir = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n"})
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])
    monkeypatch.setattr(cli, "run_stdio", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "run_http", lambda *a, **kw: None, raising=False)

    for flags in (
        ["--transport", "http"],
        ["--host", "0.0.0.0"],
        ["--port", "8765"],
        ["--token", "T"],
        ["--cert", "cert.pem"],
        ["--key", "key.pem"],
    ):
        with pytest.raises(SystemExit) as exc:
            cli.main(["--config", str(cfg), "mcp", *flags])
        assert exc.value.code == 2, f"flags {flags!r} should be rejected"


def test_mcp_health_check_failure_exits_three(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src_dir = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n"})
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])

    def fake_run_stdio(*args: object, **kwargs: object) -> None:
        raise EmbedderError("ollama not reachable")

    # _build_embedder default factory is fine; the failure surfaces from
    # within run_stdio (which is where health_check happens in production).
    monkeypatch.setattr(cli, "run_stdio", fake_run_stdio)

    rc = cli.main(["--config", str(cfg), "mcp"])

    assert rc == 3
    err = capsys.readouterr().err
    assert "ollama" in err.lower()


def test_search_sources_flag_restricts_query(
    tmp_path: Path, patch_embedder: FakeEmbedder, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n\nalpha\n"})
    code = _make_source_dir(tmp_path, "code", {"b.py": "# alpha-thing\n"})
    cfg = _write_config(tmp_path, [("vault", vault, "markdown"), ("code", code, "code")])
    cli.main(["--config", str(cfg), "index"])
    capsys.readouterr()

    cli.main(["--config", str(cfg), "search", "alpha", "--sources", "vault"])

    out = capsys.readouterr().out
    # The header line includes the source name — only vault should appear.
    score_lines = [line for line in out.splitlines() if line.startswith("score=")]
    assert score_lines
    assert all("vault" in line for line in score_lines)
    assert not any("code" in line for line in score_lines)


def test_search_output_includes_cosine(
    tmp_path: Path, patch_embedder: FakeEmbedder, capsys: pytest.CaptureFixture[str]
) -> None:
    src_dir = _make_source_dir(tmp_path, "vault", {"a.md": "# H\n\nthe quick brown fox\n"})
    cfg = _write_config(tmp_path, [("vault", src_dir, "markdown")])
    cli.main(["--config", str(cfg), "index"])
    capsys.readouterr()

    rc = cli.main(["--config", str(cfg), "search", "quick"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "cos=" in out


# Every subcommand that touches a table needs the raise, not just `index`.
# A BM25 query opens the posting list of every FTS index partition at once
# (248 files on a 40k-chunk vault, 2026-08-28), and the MCP server inherits
# the 256-file soft limit that launchd hands GUI-spawned processes — so
# `search` and `mcp` hit the ceiling exactly like `index` does.


@pytest.mark.parametrize("argv", [["index"], ["search", "quick"], ["mcp"]])
def test_table_touching_commands_raise_the_open_file_limit(
    tmp_path: Path,
    patch_embedder: FakeEmbedder,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    """Each must lift the scheduler-inherited soft limit before touching a table."""
    calls: list[bool] = []
    monkeypatch.setattr(cli, "raise_open_file_limit", lambda: calls.append(True) or 0)
    monkeypatch.setattr(cli, "run_stdio", lambda *a, **k: None)
    vault = _make_source_dir(tmp_path, "vault", {"a.md": "# A\n\nbody\n"})
    cfg = _write_config(tmp_path, [("vault", vault, "markdown")])

    rc = cli.main(["--config", str(cfg), *argv])

    assert rc == 0
    assert calls == [True]
