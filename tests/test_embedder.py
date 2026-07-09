"""Tests for local_rag.embedder — Ollama batch embedding client."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from local_rag.embedder import EmbedderError, OllamaEmbedder

URL = "http://localhost:11434"
MODEL = "bge-m3"
DIM = 1024

Handler = Callable[[httpx.Request], httpx.Response]


def _embedder(handler: Handler) -> OllamaEmbedder:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return OllamaEmbedder(URL, MODEL, DIM, client=client)


# ---------------------------------------------------------------- embed() ---


def test_embed_empty_list_does_not_call_server() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    with _embedder(handler) as e:
        assert e.embed([]) == []

    assert calls == []


def test_embed_sends_model_and_input_to_api_embed() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"embeddings": [[0.1] * DIM, [0.2] * DIM]})

    with _embedder(handler) as e:
        e.embed(["hello", "world"])

    assert captured["url"] == f"{URL}/api/embed"
    assert captured["method"] == "POST"
    assert captured["body"] == {"model": MODEL, "input": ["hello", "world"]}


def test_embed_returns_vectors_in_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"embeddings": [[float(i)] + [0.0] * (DIM - 1) for i in range(3)]},
        )

    with _embedder(handler) as e:
        result = e.embed(["a", "b", "c"])

    assert [v[0] for v in result] == [0.0, 1.0, 2.0]
    assert all(len(v) == DIM for v in result)


def test_embed_raises_when_input_exceeds_char_limit() -> None:
    """bge-m3 caps at 8192 tokens (~32k chars). Silent server-side truncation
    would degrade vectors invisibly — fail loud instead."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[0.0] * DIM]})

    with (
        _embedder(handler) as e,
        pytest.raises(EmbedderError, match=r"exceeds|too long|limit"),
    ):
        e.embed(["x" * 40_000])


def test_embed_auto_batches_large_input_lists() -> None:
    """Caller passes 150 texts in one call; embedder splits internally so no
    single HTTP request carries an unwieldy batch."""
    received_batch_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        n = len(body["input"])
        received_batch_sizes.append(n)
        return httpx.Response(200, json={"embeddings": [[0.1] * DIM] * n})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    e = OllamaEmbedder(URL, MODEL, DIM, client=client, batch_size=64)

    texts = [f"t{i}" for i in range(150)]
    result = e.embed(texts)

    assert len(result) == 150
    # 150 split into batches of 64 → [64, 64, 22]
    assert received_batch_sizes == [64, 64, 22]


def test_embed_default_batch_size_used_for_modest_input() -> None:
    """A 10-text call fits in one batch with the default batch_size."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        body = json.loads(request.content)
        return httpx.Response(200, json={"embeddings": [[0.0] * DIM] * len(body["input"])})

    with _embedder(handler) as e:
        e.embed(["x"] * 10)

    assert call_count["n"] == 1


def test_embed_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    with _embedder(handler) as e, pytest.raises(EmbedderError):
        e.embed(["a"])


def test_embed_raises_when_response_missing_embeddings_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"oops": []})

    with _embedder(handler) as e, pytest.raises(EmbedderError, match="embeddings"):
        e.embed(["a"])


def test_embed_raises_when_count_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[0.0] * DIM]})  # only 1

    with _embedder(handler) as e, pytest.raises(EmbedderError, match="2"):
        e.embed(["a", "b"])


def test_embed_raises_when_dim_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[0.0] * (DIM - 1)]})

    with _embedder(handler) as e, pytest.raises(EmbedderError, match="length"):
        e.embed(["a"])


def test_embed_raises_on_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated", request=request)

    with _embedder(handler) as e, pytest.raises(EmbedderError):
        e.embed(["a"])


# --------------------------------------------------------- health_check() ---


def test_health_check_passes_when_model_tagged_latest() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "bge-m3:latest"}, {"name": "llama3"}]})

    with _embedder(handler) as e:
        e.health_check()


def test_health_check_passes_when_model_exact_match() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "bge-m3"}]})

    with _embedder(handler) as e:
        e.health_check()


def test_health_check_raises_when_model_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "llama3:latest"}]})

    with _embedder(handler) as e, pytest.raises(EmbedderError, match=r"bge-m3"):
        e.health_check()


def test_health_check_message_includes_pull_hint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": []})

    with _embedder(handler) as e:
        with pytest.raises(EmbedderError) as excinfo:
            e.health_check()
        assert "ollama pull bge-m3" in str(excinfo.value)


def test_health_check_raises_when_server_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated", request=request)

    with _embedder(handler) as e, pytest.raises(EmbedderError):
        e.health_check()


def test_health_check_raises_on_5xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with _embedder(handler) as e, pytest.raises(EmbedderError):
        e.health_check()


# -------------------------------------------------- verify_normalization() ---


def test_verify_normalization_passes_on_unit_vector() -> None:
    unit = [1.0] + [0.0] * (DIM - 1)  # ‖v‖ = 1

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [unit]})

    with _embedder(handler) as e:
        e.verify_normalization()


def test_verify_normalization_raises_when_unnormalized() -> None:
    bad = [2.0] + [0.0] * (DIM - 1)  # ‖v‖ = 2.0

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [bad]})

    with _embedder(handler) as e:
        with pytest.raises(EmbedderError) as excinfo:
            e.verify_normalization()
        # actual norm shows up in the message so the user can diagnose
        assert "2.00" in str(excinfo.value) or "2.0" in str(excinfo.value)


# ------------------------------------------------------------- warm_up() ---


def test_warm_up_makes_single_request() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"embeddings": [[0.0] * DIM]})

    with _embedder(handler) as e:
        e.warm_up()

    assert call_count == 1


# ---------------------------------------------------------- lifecycle ---


def test_context_manager_does_not_close_injected_client() -> None:
    injected = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with OllamaEmbedder(URL, MODEL, DIM, client=injected):
        pass
    assert not injected.is_closed
    injected.close()


def test_context_manager_closes_owned_client() -> None:
    with OllamaEmbedder(URL, MODEL, DIM) as e:
        owned = e._client
    assert owned.is_closed


# ----------------------------------------- integration (skipped if no Ollama) ---


def _bge_m3_available() -> bool:
    try:
        r = httpx.get(f"{URL}/api/tags", timeout=1.0)
        if r.status_code != 200:
            return False
        names = {m.get("name", "") for m in r.json().get("models", [])}
        bare = {n.split(":")[0] for n in names}
        return MODEL in names or MODEL in bare
    except (httpx.HTTPError, OSError):
        return False


requires_ollama = pytest.mark.skipif(
    not _bge_m3_available(),
    reason=f"Ollama not reachable at {URL} or {MODEL!r} not pulled",
)


@requires_ollama
def test_integration_health_check_passes() -> None:
    with OllamaEmbedder(URL, MODEL, DIM) as e:
        e.health_check()


@requires_ollama
def test_integration_embed_returns_correct_dim() -> None:
    with OllamaEmbedder(URL, MODEL, DIM) as e:
        result = e.embed(["hello world"])
    assert len(result) == 1
    assert len(result[0]) == DIM


@requires_ollama
def test_integration_vectors_are_normalized() -> None:
    with OllamaEmbedder(URL, MODEL, DIM) as e:
        e.verify_normalization()
