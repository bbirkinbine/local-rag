"""Tests for local_rag.config — TOML config loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_rag.config import Config, ConfigError
from local_rag.store import DEFAULT_KEEP_RUNS


def _write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    d = tmp_path / "vault"
    d.mkdir()
    return d


@pytest.fixture
def code_dir(tmp_path: Path) -> Path:
    d = tmp_path / "code"
    d.mkdir()
    return d


def _minimal_toml(tmp_path: Path, vault_dir: Path) -> str:
    return f"""
db_path = "{tmp_path}/db"

[embedding]
provider = "ollama"
model = "bge-m3"
url = "http://localhost:11434"
dim = 1024

[[sources]]
name = "vault"
path = "{vault_dir}"
type = "markdown"
"""


def test_loads_minimal_valid_config(tmp_path: Path, vault_dir: Path) -> None:
    cfg_path = _write(tmp_path / "config.toml", _minimal_toml(tmp_path, vault_dir))

    config = Config.load(cfg_path)

    assert config.db_path == (tmp_path / "db").resolve()
    assert config.embedding.provider == "ollama"
    assert config.embedding.model == "bge-m3"
    assert config.embedding.url == "http://localhost:11434"
    assert config.embedding.dim == 1024
    assert len(config.sources) == 1
    assert config.sources[0].name == "vault"
    assert config.sources[0].type == "markdown"
    assert config.sources[0].path == vault_dir.resolve()


def test_source_defaults_ignore_and_gitignore_to_safe_values(
    tmp_path: Path, vault_dir: Path
) -> None:
    cfg_path = _write(tmp_path / "config.toml", _minimal_toml(tmp_path, vault_dir))

    source = Config.load(cfg_path).sources[0]

    assert source.ignore == []
    assert source.respect_gitignore is False


def test_expands_home_in_db_path(
    tmp_path: Path, vault_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_path = _write(
        tmp_path / "config.toml",
        f"""
db_path = "~/db"

[embedding]
provider = "ollama"
model = "bge-m3"
url = "http://localhost:11434"
dim = 1024

[[sources]]
name = "vault"
path = "{vault_dir}"
type = "markdown"
""",
    )

    config = Config.load(cfg_path)

    assert config.db_path == (tmp_path / "db").resolve()


def test_expands_home_in_source_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "myvault").mkdir()
    cfg_path = _write(
        tmp_path / "config.toml",
        f"""
db_path = "{tmp_path}/db"

[embedding]
provider = "ollama"
model = "bge-m3"
url = "http://localhost:11434"
dim = 1024

[[sources]]
name = "vault"
path = "~/myvault"
type = "markdown"
""",
    )

    config = Config.load(cfg_path)

    assert config.sources[0].path == (tmp_path / "myvault").resolve()


def test_missing_db_path_raises(tmp_path: Path, vault_dir: Path) -> None:
    cfg_path = _write(
        tmp_path / "config.toml",
        f"""
[embedding]
provider = "ollama"
model = "bge-m3"
url = "http://localhost:11434"
dim = 1024

[[sources]]
name = "vault"
path = "{vault_dir}"
type = "markdown"
""",
    )

    with pytest.raises(ConfigError, match="db_path"):
        Config.load(cfg_path)


def test_missing_embedding_section_raises(tmp_path: Path, vault_dir: Path) -> None:
    cfg_path = _write(
        tmp_path / "config.toml",
        f"""
db_path = "{tmp_path}/db"

[[sources]]
name = "vault"
path = "{vault_dir}"
type = "markdown"
""",
    )

    with pytest.raises(ConfigError, match="embedding"):
        Config.load(cfg_path)


def test_missing_embedding_dim_raises(tmp_path: Path, vault_dir: Path) -> None:
    cfg_path = _write(
        tmp_path / "config.toml",
        f"""
db_path = "{tmp_path}/db"

[embedding]
provider = "ollama"
model = "bge-m3"
url = "http://localhost:11434"

[[sources]]
name = "vault"
path = "{vault_dir}"
type = "markdown"
""",
    )

    with pytest.raises(ConfigError, match="dim"):
        Config.load(cfg_path)


def test_missing_sources_raises(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path / "config.toml",
        f"""
db_path = "{tmp_path}/db"

[embedding]
provider = "ollama"
model = "bge-m3"
url = "http://localhost:11434"
dim = 1024
""",
    )

    with pytest.raises(ConfigError, match="sources"):
        Config.load(cfg_path)


def test_empty_sources_list_raises(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path / "config.toml",
        f"""
db_path = "{tmp_path}/db"

[embedding]
provider = "ollama"
model = "bge-m3"
url = "http://localhost:11434"
dim = 1024

sources = []
""",
    )

    with pytest.raises(ConfigError, match="sources"):
        Config.load(cfg_path)


def test_invalid_source_type_raises(tmp_path: Path, vault_dir: Path) -> None:
    cfg_path = _write(
        tmp_path / "config.toml",
        f"""
db_path = "{tmp_path}/db"

[embedding]
provider = "ollama"
model = "bge-m3"
url = "http://localhost:11434"
dim = 1024

[[sources]]
name = "vault"
path = "{vault_dir}"
type = "screenshots"
""",
    )

    with pytest.raises(ConfigError, match="type"):
        Config.load(cfg_path)


def test_nonexistent_source_path_raises(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path / "config.toml",
        f"""
db_path = "{tmp_path}/db"

[embedding]
provider = "ollama"
model = "bge-m3"
url = "http://localhost:11434"
dim = 1024

[[sources]]
name = "missing"
path = "{tmp_path}/does-not-exist"
type = "markdown"
""",
    )

    with pytest.raises(ConfigError, match=r"does not exist|path"):
        Config.load(cfg_path)


def test_source_path_that_is_file_not_dir_raises(tmp_path: Path) -> None:
    (tmp_path / "afile.md").write_text("hi")
    cfg_path = _write(
        tmp_path / "config.toml",
        f"""
db_path = "{tmp_path}/db"

[embedding]
provider = "ollama"
model = "bge-m3"
url = "http://localhost:11434"
dim = 1024

[[sources]]
name = "f"
path = "{tmp_path}/afile.md"
type = "markdown"
""",
    )

    with pytest.raises(ConfigError, match="directory"):
        Config.load(cfg_path)


def test_duplicate_source_names_raise(tmp_path: Path, vault_dir: Path, code_dir: Path) -> None:
    cfg_path = _write(
        tmp_path / "config.toml",
        f"""
db_path = "{tmp_path}/db"

[embedding]
provider = "ollama"
model = "bge-m3"
url = "http://localhost:11434"
dim = 1024

[[sources]]
name = "dup"
path = "{vault_dir}"
type = "markdown"

[[sources]]
name = "dup"
path = "{code_dir}"
type = "code"
""",
    )

    with pytest.raises(ConfigError, match="dup"):
        Config.load(cfg_path)


def test_invalid_toml_raises_config_error(tmp_path: Path) -> None:
    cfg_path = _write(tmp_path / "config.toml", "this is = not valid [toml]]\n")

    with pytest.raises(ConfigError):
        Config.load(cfg_path)


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        Config.load(tmp_path / "does-not-exist.toml")


def test_dim_as_boolean_raises_config_error(tmp_path: Path, vault_dir: Path) -> None:
    """`dim = true` is technically int-typed in Python (bool ⊂ int) — reject it."""
    cfg_path = _write(
        tmp_path / "config.toml",
        f"""
db_path = "{tmp_path}/db"

[embedding]
provider = "ollama"
model = "bge-m3"
url = "http://localhost:11434"
dim = true

[[sources]]
name = "v"
path = "{vault_dir}"
type = "markdown"
""",
    )

    with pytest.raises(ConfigError, match=r"dim"):
        Config.load(cfg_path)


def test_config_file_with_utf8_bom_is_handled(tmp_path: Path, vault_dir: Path) -> None:
    body = f"""
db_path = "{tmp_path}/db"

[embedding]
provider = "ollama"
model = "bge-m3"
url = "http://localhost:11434"
dim = 1024

[[sources]]
name = "v"
path = "{vault_dir}"
type = "markdown"
"""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_bytes("﻿".encode() + body.encode("utf-8"))

    config = Config.load(cfg_path)

    assert config.embedding.model == "bge-m3"


def test_sources_as_non_list_raises(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path / "config.toml",
        f"""
db_path = "{tmp_path}/db"

[embedding]
provider = "ollama"
model = "bge-m3"
url = "http://localhost:11434"
dim = 1024

sources = "vault"
""",
    )

    with pytest.raises(ConfigError, match=r"sources"):
        Config.load(cfg_path)


def test_ignore_with_non_string_entry_raises(tmp_path: Path, vault_dir: Path) -> None:
    cfg_path = _write(
        tmp_path / "config.toml",
        f"""
db_path = "{tmp_path}/db"

[embedding]
provider = "ollama"
model = "bge-m3"
url = "http://localhost:11434"
dim = 1024

[[sources]]
name = "v"
path = "{vault_dir}"
type = "markdown"
ignore = ["ok", 42]
""",
    )

    with pytest.raises(ConfigError, match=r"ignore"):
        Config.load(cfg_path)


def test_sources_preserve_toml_order(tmp_path: Path, vault_dir: Path, code_dir: Path) -> None:
    cfg_path = _write(
        tmp_path / "config.toml",
        f"""
db_path = "{tmp_path}/db"

[embedding]
provider = "ollama"
model = "bge-m3"
url = "http://localhost:11434"
dim = 1024

[[sources]]
name = "first"
path = "{vault_dir}"
type = "markdown"

[[sources]]
name = "second"
path = "{code_dir}"
type = "code"
respect_gitignore = true
ignore = ["build/", "dist/"]
""",
    )

    config = Config.load(cfg_path)

    assert [s.name for s in config.sources] == ["first", "second"]
    assert config.sources[1].respect_gitignore is True
    assert config.sources[1].ignore == ["build/", "dist/"]


# ------------------------------------------------------------ [store] block ---
# Retention is bounded in runs, not hours: old copies are produced per
# indexing run, so a run count means the same thing at every cadence.


def test_store_block_absent_uses_the_default_keep_runs(tmp_path: Path, vault_dir: Path) -> None:
    """The block is optional — omitting it must not be an error."""
    cfg_path = _write(tmp_path / "config.toml", _minimal_toml(tmp_path, vault_dir))

    config = Config.load(cfg_path)

    assert config.store.keep_runs == DEFAULT_KEEP_RUNS


def test_store_keep_runs_is_parsed(tmp_path: Path, vault_dir: Path) -> None:
    cfg_path = _write(
        tmp_path / "config.toml",
        _minimal_toml(tmp_path, vault_dir) + "\n[store]\nkeep_runs = 40\n",
    )

    config = Config.load(cfg_path)

    assert config.store.keep_runs == 40


def test_store_keep_runs_zero_is_allowed(tmp_path: Path, vault_dir: Path) -> None:
    """Zero means keep nothing reclaimable — a valid choice, not an error."""
    cfg_path = _write(
        tmp_path / "config.toml",
        _minimal_toml(tmp_path, vault_dir) + "\n[store]\nkeep_runs = 0\n",
    )

    config = Config.load(cfg_path)

    assert config.store.keep_runs == 0


def test_store_keep_runs_rejects_negative(tmp_path: Path, vault_dir: Path) -> None:
    cfg_path = _write(
        tmp_path / "config.toml",
        _minimal_toml(tmp_path, vault_dir) + "\n[store]\nkeep_runs = -1\n",
    )

    with pytest.raises(ConfigError, match="keep_runs"):
        Config.load(cfg_path)


def test_store_keep_runs_rejects_bool(tmp_path: Path, vault_dir: Path) -> None:
    """`True` is an int in Python; it must not slip through as 1 run."""
    cfg_path = _write(
        tmp_path / "config.toml",
        _minimal_toml(tmp_path, vault_dir) + "\n[store]\nkeep_runs = true\n",
    )

    with pytest.raises(ConfigError, match="keep_runs"):
        Config.load(cfg_path)


def test_store_keep_runs_rejects_fractional(tmp_path: Path, vault_dir: Path) -> None:
    """Runs are discrete; half a run is a typo, not a policy."""
    cfg_path = _write(
        tmp_path / "config.toml",
        _minimal_toml(tmp_path, vault_dir) + "\n[store]\nkeep_runs = 2.5\n",
    )

    with pytest.raises(ConfigError, match="keep_runs"):
        Config.load(cfg_path)


def test_store_keep_runs_rejects_string(tmp_path: Path, vault_dir: Path) -> None:
    cfg_path = _write(
        tmp_path / "config.toml",
        _minimal_toml(tmp_path, vault_dir) + '\n[store]\nkeep_runs = "24"\n',
    )

    with pytest.raises(ConfigError, match="keep_runs"):
        Config.load(cfg_path)


def test_store_block_must_be_a_table(tmp_path: Path, vault_dir: Path) -> None:
    # Prepended, not appended: a bare key after `[[sources]]` would belong to
    # that table rather than the document root.
    cfg_path = _write(
        tmp_path / "config.toml",
        'store = "nope"\n' + _minimal_toml(tmp_path, vault_dir),
    )

    with pytest.raises(ConfigError, match="store"):
        Config.load(cfg_path)
