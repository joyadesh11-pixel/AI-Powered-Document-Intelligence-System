"""GeminiEmbedder must ask the API for EMBED_DIM-sized vectors on every call."""
from __future__ import annotations

from app.pipeline.config import get_config
from app.services.embedder import GeminiEmbedder


class _StubClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def embed_documents(self, texts, **kwargs):
        self.calls.append(kwargs)
        return [[0.0] * kwargs.get("output_dimensionality", 3072) for _ in texts]


def _embedder() -> tuple[GeminiEmbedder, _StubClient]:
    emb = GeminiEmbedder()
    stub = _StubClient()
    emb._client = lambda: stub   # no network, no API key
    return emb, stub


def test_every_call_requests_the_configured_dimension():
    emb, stub = _embedder()
    vectors = emb.embed(["a", "b"])
    assert stub.calls[0]["output_dimensionality"] == get_config().EMBED_DIM == 768
    assert all(len(v) == 768 for v in vectors)


def test_task_type_is_passed_per_call():
    emb, stub = _embedder()
    emb.embed(["doc"], task_type="RETRIEVAL_DOCUMENT")
    emb.embed(["query"], task_type="RETRIEVAL_QUERY")
    assert [c["task_type"] for c in stub.calls] == ["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]
