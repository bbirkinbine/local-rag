"""``config.example.toml`` stays valid, current, and in sync with the docs.

The example's placeholder paths won't exist on a CI machine, and ``Config``
validates that source paths are real directories — so tests that load the
example first redirect every path into ``tmp_path``.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from local_rag.config import Config
from local_rag.store import DEFAULT_KEEP_RUNS

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = REPO_ROOT / "config.example.toml"
DOC_PATH = REPO_ROOT / "docs" / "configuration.md"


def _load_with_real_paths(tmp_path: Path) -> Config:
    """Load the example config with its paths redirected into ``tmp_path``."""
    text = EXAMPLE_PATH.read_text(encoding="utf-8")
    text = re.sub(
        r'^db_path = ".*?"',
        f'db_path = "{tmp_path / "db"}"',
        text,
        flags=re.MULTILINE,
    )

    counter = iter(range(1000))

    def _next_source_dir(_match: re.Match[str]) -> str:
        src_dir = tmp_path / f"source-{next(counter)}"
        src_dir.mkdir()
        return f'path = "{src_dir}"'

    text = re.sub(r'^path = ".*?"', _next_source_dir, text, flags=re.MULTILINE)

    rewritten = tmp_path / "config.toml"
    rewritten.write_text(text, encoding="utf-8")
    return Config.load(rewritten)


def test_example_file_loads_cleanly(tmp_path: Path) -> None:
    config = _load_with_real_paths(tmp_path)
    assert len(config.sources) > 0


def test_example_keep_runs_is_the_shipped_default(tmp_path: Path) -> None:
    config = _load_with_real_paths(tmp_path)
    assert config.store.keep_runs == DEFAULT_KEEP_RUNS


def test_example_states_store_defaults_explicitly() -> None:
    raw = tomllib.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    assert raw["store"]["keep_runs"] == DEFAULT_KEEP_RUNS


def test_doc_example_stays_in_sync_with_example_file() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")
    match = re.search(r"```toml\n(.*?)```", doc, flags=re.DOTALL)
    assert match is not None, "docs/configuration.md has no ```toml example block"
    doc_data = tomllib.loads(match.group(1))
    example_data = tomllib.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    assert doc_data == example_data
