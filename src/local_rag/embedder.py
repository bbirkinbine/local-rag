"""Ollama HTTP client for batch embeddings.

Talks to Ollama's batch ``POST /api/embed`` endpoint. Synchronous; the MCP
server (later slice) can offload to a thread if needed.

The embedder owns its ``httpx.Client`` by default, but a client may be
injected for testing (e.g. ``httpx.MockTransport``) — in that case the
caller is responsible for closing it.
"""

from __future__ import annotations

import math
from types import TracebackType
from typing import Any, Self

import httpx

_NORM_TOLERANCE = 1e-3
_WARMUP_TEXT = "warmup"
_NORMALIZATION_PROBE_TEXT = "normalization check"


class EmbedderError(Exception):
    """Raised when the embedder cannot reach Ollama or produce valid embeddings."""


class OllamaEmbedder:
    """Synchronous client for Ollama's batch ``/api/embed`` endpoint."""

    def __init__(
        self,
        url: str,
        model: str,
        dim: int,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._model = model
        self._dim = dim
        if client is None:
            self._client = httpx.Client(timeout=timeout)
            self._owns_client = True
        else:
            self._client = client
            self._owns_client = False

    # ------------------------------------------------------------- lifecycle

    def close(self) -> None:
        """Close the owned ``httpx.Client``. No-op for an injected client."""
        if self._owns_client and not self._client.is_closed:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --------------------------------------------------------- public API

    def health_check(self) -> None:
        """Verify Ollama is reachable and the configured model is available.

        Raises:
            EmbedderError: with a clear ``ollama pull <model>`` hint if the
                model is missing, or a transport-level message if the server
                is unreachable.
        """
        try:
            r = self._client.get(f"{self._url}/api/tags")
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise EmbedderError(f"cannot reach Ollama at {self._url}: {e}") from e

        try:
            payload = r.json()
        except ValueError as e:
            raise EmbedderError(f"Ollama /api/tags returned non-JSON: {e}") from e

        raw_models = payload.get("models") or []
        names: set[str] = set()
        for entry in raw_models:
            if isinstance(entry, dict):
                name = entry.get("name")
                if isinstance(name, str):
                    names.add(name)
                    names.add(name.split(":", 1)[0])  # tolerate "name:latest"

        if self._model not in names:
            raise EmbedderError(
                f"model {self._model!r} not available on Ollama at {self._url}; "
                f"run: ollama pull {self._model}"
            )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns one vector per input, same order."""
        if not texts:
            return []

        try:
            r = self._client.post(
                f"{self._url}/api/embed",
                json={"model": self._model, "input": texts},
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise EmbedderError(f"embed request to {self._url}/api/embed failed: {e}") from e

        try:
            payload = r.json()
        except ValueError as e:
            raise EmbedderError(f"Ollama /api/embed returned non-JSON: {e}") from e

        if not isinstance(payload, dict):
            raise EmbedderError(f"Ollama /api/embed returned non-object: {type(payload).__name__}")
        return self._parse_embeddings(payload, expected_count=len(texts))

    def warm_up(self) -> None:
        """Issue a single embed to force-load the model so the first real call is fast."""
        self.embed([_WARMUP_TEXT])

    def verify_normalization(self) -> None:
        """Embed a sentinel and assert ``|norm(v) - 1.0| < 1e-3``.

        bge-m3 (and most modern embedders) return L2-normalized vectors, which
        the rest of the pipeline assumes (we use cosine distance). If this ever
        regresses — model swap, config typo, transport encoding bug — cosine
        results degrade silently. This one-shot probe surfaces it loudly.
        """
        vec = self.embed([_NORMALIZATION_PROBE_TEXT])[0]
        norm = math.sqrt(sum(x * x for x in vec))
        if abs(norm - 1.0) > _NORM_TOLERANCE:
            raise EmbedderError(
                f"model {self._model!r} returned unnormalized embedding "
                f"(norm = {norm:.4f}); cosine search assumes normalization"
            )

    # ----------------------------------------------------------- internal

    def _parse_embeddings(
        self, payload: dict[str, Any], *, expected_count: int
    ) -> list[list[float]]:
        embeddings_raw = payload.get("embeddings")
        if not isinstance(embeddings_raw, list):
            raise EmbedderError("missing 'embeddings' list in Ollama response")
        if len(embeddings_raw) != expected_count:
            raise EmbedderError(f"expected {expected_count} embeddings, got {len(embeddings_raw)}")

        result: list[list[float]] = []
        for i, vec in enumerate(embeddings_raw):
            if not isinstance(vec, list):
                raise EmbedderError(f"embedding {i}: expected list, got {type(vec).__name__}")
            if len(vec) != self._dim:
                raise EmbedderError(f"embedding {i}: expected length {self._dim}, got {len(vec)}")
            if not all(isinstance(x, (int, float)) for x in vec):
                raise EmbedderError(f"embedding {i}: contains non-numeric values")
            result.append([float(x) for x in vec])
        return result
