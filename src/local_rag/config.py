"""Load and validate the local-rag TOML config.

Parsing is stdlib-only: ``tomllib`` + ``dataclasses`` + ``pathlib``, with
validation hand-rolled to keep error messages user-facing and the dependency
footprint zero. The one package import is ``store``'s run-retention default,
so the shipped value is defined once rather than restated here.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from local_rag.store import DEFAULT_KEEP_RUNS

SourceType = Literal["markdown", "code"]
_VALID_SOURCE_TYPES: frozenset[str] = frozenset({"markdown", "code"})


class ConfigError(Exception):
    """Raised when the config file is missing, unreadable, or invalid."""


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str
    model: str
    url: str
    dim: int


@dataclass(frozen=True)
class Source:
    name: str
    path: Path
    type: SourceType
    ignore: list[str] = field(default_factory=list)
    respect_gitignore: bool = False


@dataclass(frozen=True)
class StoreConfig:
    """Tunables for the LanceDB store. Every field has a shipped default."""

    keep_runs: int = DEFAULT_KEEP_RUNS


@dataclass(frozen=True)
class Config:
    db_path: Path
    embedding: EmbeddingConfig
    sources: list[Source]
    store: StoreConfig = field(default_factory=StoreConfig)

    @classmethod
    def load(cls, path: Path) -> Config:
        """Parse and validate a config file from ``path``."""
        try:
            raw_bytes = path.read_bytes()
        except FileNotFoundError as e:
            raise ConfigError(f"config file not found: {path}") from e
        except OSError as e:
            raise ConfigError(f"could not read config file {path}: {e}") from e

        # `utf-8-sig` strips a leading BOM if present (hand-edited configs on
        # Windows sometimes carry one); on BOM-free files it behaves like utf-8.
        try:
            text = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise ConfigError(f"config file {path} is not valid UTF-8: {e}") from e
        try:
            raw = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"invalid TOML in {path}: {e}") from e

        return cls._from_dict(raw)

    @classmethod
    def _from_dict(cls, raw: dict[str, Any]) -> Config:
        db_path = _expand_path(_require(raw, "db_path", str))
        embedding = _parse_embedding(_require(raw, "embedding", dict))
        sources = _parse_sources(_require(raw, "sources", list))
        store = _parse_store(raw.get("store"))
        return cls(db_path=db_path, embedding=embedding, sources=sources, store=store)


def _require[T](obj: dict[str, Any], key: str, kind: type[T]) -> T:
    if key not in obj:
        raise ConfigError(f"missing required field: {key!r}")
    value = obj[key]
    # Python's `bool` is a subclass of `int`, so `isinstance(True, int)` is True.
    # Reject bools when an int is expected so `dim = true` doesn't slip past.
    if kind is int and isinstance(value, bool):
        raise ConfigError(f"field {key!r} must be a int, got bool")
    if not isinstance(value, kind):
        raise ConfigError(f"field {key!r} must be a {kind.__name__}, got {type(value).__name__}")
    return value


def _expand_path(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def _parse_embedding(d: dict[str, Any]) -> EmbeddingConfig:
    return EmbeddingConfig(
        provider=_require(d, "provider", str),
        model=_require(d, "model", str),
        url=_require(d, "url", str),
        dim=_require(d, "dim", int),
    )


def _parse_store(raw: Any) -> StoreConfig:  # noqa: ANN401 — arbitrary TOML value
    """Parse the optional ``[store]`` block.

    Absent means "take every shipped default", so configs written before the
    block existed keep loading unchanged.

    Args:
        raw: The ``store`` value from the parsed TOML, or ``None`` if absent.

    Raises:
        ConfigError: If ``store`` is not a table, or a key is out of range.
    """
    if raw is None:
        return StoreConfig()
    if not isinstance(raw, dict):
        raise ConfigError(f"field 'store' must be a table, got {type(raw).__name__}")

    if "keep_runs" not in raw:
        return StoreConfig()

    runs = raw["keep_runs"]
    # `bool` is a subclass of `int`, so `keep_runs = true` would otherwise
    # read as one run. Floats are rejected outright: runs are discrete, so
    # `2.5` is a typo rather than a policy.
    if isinstance(runs, bool) or not isinstance(runs, int):
        raise ConfigError(f"field 'keep_runs' must be an integer, got {type(runs).__name__}")
    if runs < 0:
        raise ConfigError(f"field 'keep_runs' must be >= 0, got {runs}")

    return StoreConfig(keep_runs=runs)


def _parse_sources(items: list[Any]) -> list[Source]:
    if not items:
        raise ConfigError("at least one [[sources]] block is required")

    parsed: list[Source] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ConfigError(f"each [[sources]] entry must be a table, got {type(item).__name__}")
        source = _parse_source(item)
        if source.name in seen:
            raise ConfigError(f"duplicate source name: {source.name!r}")
        seen.add(source.name)
        parsed.append(source)
    return parsed


def _parse_source(d: dict[str, Any]) -> Source:
    name = _require(d, "name", str)
    raw_type = _require(d, "type", str)
    if raw_type not in _VALID_SOURCE_TYPES:
        raise ConfigError(
            f"source {name!r}: invalid type {raw_type!r}; "
            f"must be one of {sorted(_VALID_SOURCE_TYPES)}"
        )

    path = _expand_path(_require(d, "path", str))
    if not path.exists():
        raise ConfigError(f"source {name!r}: path does not exist: {path}")
    if not path.is_dir():
        raise ConfigError(f"source {name!r}: path is not a directory: {path}")

    ignore_raw = d.get("ignore", [])
    if not isinstance(ignore_raw, list) or not all(isinstance(p, str) for p in ignore_raw):
        raise ConfigError(f"source {name!r}: 'ignore' must be a list of strings")

    respect = d.get("respect_gitignore", False)
    if not isinstance(respect, bool):
        raise ConfigError(f"source {name!r}: 'respect_gitignore' must be a boolean")

    return Source(
        name=name,
        path=path,
        type=cast(SourceType, raw_type),
        ignore=list(ignore_raw),
        respect_gitignore=respect,
    )
