# Slice 01 — config loader + paths

First implementation slice. Dependency-free (stdlib only). Establishes the typed surface every later slice reads settings against.

## Goal

Implement two modules:

- `local_rag.config` — loads and validates the TOML config from disk into typed dataclasses.
- `local_rag.paths` — canonical filesystem paths (default config location, env-override).

## Success criteria

### `local_rag.config`

- `Config.load(path: Path) -> Config` parses a TOML file into a `Config` dataclass.
- `Config` fields:
  - `db_path: Path` — `~`-expanded, absolute.
  - `embedding: EmbeddingConfig` — `provider: str`, `model: str`, `url: str`, `dim: int`.
  - `sources: list[Source]` — order preserved from the TOML.
- `Source` fields:
  - `name: str` — unique across all sources.
  - `path: Path` — `~`-expanded, absolute, must exist and be a directory.
  - `type: Literal["markdown", "code"]`.
  - `ignore: list[str]` — defaults to `[]`.
  - `respect_gitignore: bool` — defaults to `False`.

### Validation errors (clear messages, dedicated exception class `ConfigError`)

- Missing required top-level field (`db_path`, `embedding`, `sources`).
- Missing required `embedding` field (`provider`, `model`, `url`, `dim`).
- Missing required `[[sources]]` field (`name`, `path`, `type`).
- Invalid TOML (let `tomllib.TOMLDecodeError` propagate wrapped as `ConfigError`).
- Source `type` not in `{"markdown", "code"}`.
- Source `path` does not exist or is not a directory.
- Duplicate source `name` values.
- File-not-found / unreadable config file.

### `local_rag.paths`

- `default_config_path() -> Path` — returns `~/.config/local-rag/config.toml`, expanded + absolute.
- `config_path_from_env_or_default() -> Path` — returns `Path(os.environ["LOCAL_RAG_CONFIG"])` (expanded + absolute) if set and non-empty, else `default_config_path()`.

## Non-goals

- No config writing or scaffolding.
- No connection to LanceDB or Ollama.
- No actual indexing.
- No watch / hot reload.
- No CLI wiring yet (slice 6 owns the CLI surface).

## Files to be touched

- `src/local_rag/__init__.py` (new, minimal)
- `src/local_rag/config.py` (new)
- `src/local_rag/paths.py` (new)
- `tests/__init__.py` (new, empty)
- `tests/test_config.py` (new)
- `tests/test_paths.py` (new)

## Implementation notes (constraints)

- **Stdlib only.** Use `tomllib` (stdlib in 3.12), `dataclasses`, `pathlib`, `typing.Literal`, `os`.
- **No pydantic, no attrs.** Hand-rolled validation in `Config.load`.
- **Files ≤ 300 lines** each (per CLAUDE.md).
- **Type-strict.** Survives `mypy --strict`.
- **`structlog` not used in this slice** (no observable behavior to log; comes online with the indexer slice).

## Verification

- `uv run pytest tests/test_config.py tests/test_paths.py -v` — all green.
- `uv run ruff check src/ tests/` — clean.
- `uv run ruff format --check src/ tests/` — clean.
- `uv run mypy src/` — clean (strict mode).
- Manual sanity: load the `[Sources — initial config]` block from `docs/specs/local-rag.md` (with `~` expansion working against the user's actual vault path) and confirm sources parse.
