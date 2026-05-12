# Slice 02 — Ollama embedder client

Second implementation slice. Adds the embedding layer between text and the eventual LanceDB store.

## Goal

Implement `local_rag.embedder.OllamaEmbedder`: a synchronous HTTP client for Ollama's batch `/api/embed` endpoint, with health-check, normalization sanity check, and a warm-up call.

## Success criteria

### Public API (`OllamaEmbedder`)

- `__init__(url: str, model: str, dim: int, timeout: float = 60.0, client: httpx.Client | None = None)`
  - `client` is injectable for tests (via `httpx.MockTransport`). When `None`, the embedder constructs and owns an `httpx.Client(timeout=...)`.
- `embed(texts: list[str]) -> list[list[float]]`
  - Empty input → empty output (no request sent).
  - Sends one POST to `<url>/api/embed` with `{"model": ..., "input": texts}`.
  - Returns a list of `dim`-length float vectors, same length and order as `texts`.
- `health_check() -> None`
  - GETs `<url>/api/tags`. Raises `EmbedderError` if unreachable.
  - Raises `EmbedderError` if the configured `model` is not in the available list. Message must include `ollama pull <model>` so the user knows how to fix it.
- `warm_up() -> None`
  - Single embed of one short token. Forces Ollama to load the model so the first real embed is fast.
- `verify_normalization() -> None`
  - Embeds one sentinel string and asserts `|‖v‖₂ − 1.0| < 1e-3`. Raises `EmbedderError` with the actual norm in the message if unnormalized.
- Context manager: `__enter__` / `__exit__` / `close()`. `close()` closes the owned `httpx.Client`.

### `EmbedderError`

Dedicated exception class. Raised for:

- Network failure / non-2xx from Ollama (with the underlying error preserved via `__cause__`).
- Model not available on the configured server.
- Response shape mismatch (missing `embeddings`, wrong number of vectors, wrong vector dim).
- Unnormalized vectors detected by `verify_normalization`.

### Behavioral rules (from the project spec)

- **Stdlib-only L2 norm.** Use `math.sqrt(sum(x*x for x in v))`. No `numpy` dep in this slice (numpy comes in transitively with LanceDB later, but the embedder doesn't need it).
- **Sync.** The MCP server (later slice) can offload via `asyncio.to_thread`. Sync keeps the API and tests simple.
- **No logging in this slice.** structlog gets wired in the CLI / MCP slice once we have a stderr-only configuration story (avoid corrupting the MCP stdio transport).

## Non-goals

- No retry logic (single attempt; failures propagate as `EmbedderError`).
- No streaming embed.
- No connection pooling tweaks beyond httpx defaults.
- No model auto-pull on `health_check` failure (the message tells the user the command).
- No async API.

## Files

- `src/local_rag/embedder.py` (new)
- `tests/test_embedder.py` (new)

## Tests

### Unit (always run; use `httpx.MockTransport` — no network)

- `embed` sends `{"model": ..., "input": [...]}` to `/api/embed`.
- `embed` returns parsed vectors with correct length and order.
- `embed([])` returns `[]` without hitting the network.
- `embed` raises `EmbedderError` on HTTP error.
- `embed` raises `EmbedderError` when response is missing `embeddings`.
- `embed` raises `EmbedderError` when the response vector count ≠ input count.
- `embed` raises `EmbedderError` when a vector's length ≠ `dim`.
- `health_check` passes when `/api/tags` returns the model (matches both `bge-m3` and `bge-m3:latest` styles).
- `health_check` raises `EmbedderError` (with the model name and `ollama pull` hint) when the model is missing.
- `health_check` raises `EmbedderError` when `/api/tags` is unreachable / 5xx.
- `verify_normalization` passes on a unit vector.
- `verify_normalization` raises with the actual norm in the message when vector is unnormalized.
- `warm_up` issues exactly one embed request.
- Context-manager close: leaving the `with` block closes the owned client; `client=` injection means the embedder does *not* close the user's client.

### Integration (skip when Ollama or `bge-m3` is unavailable)

- Real `health_check()` against `http://localhost:11434` with `bge-m3` succeeds.
- Real `embed(["hello world"])` returns one vector of dim 1024.
- Real `verify_normalization()` passes against bge-m3.

Skip with a module-level `pytest.mark.skipif` driven by a `httpx.get("http://localhost:11434/api/tags", timeout=1.0)` probe.

## Verification

```
uv run pytest tests/ -v           # 22 prior + ~14 new unit + ~3 integration
uv run ruff check src/ tests/     # clean
uv run mypy src/                  # clean (strict)
```

Manual smoke: from a Python shell against the user's Ollama:

```python
from local_rag.embedder import OllamaEmbedder
with OllamaEmbedder("http://localhost:11434", "bge-m3", 1024) as e:
    e.health_check()
    e.verify_normalization()
    print(len(e.embed(["hello", "world"])[0]))  # 1024
```
