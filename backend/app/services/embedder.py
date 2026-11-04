"""Embedder protocol and the Gemini implementation.

EMBED_MODEL and EMBED_DIM come exclusively from config, never hardcoded here.
GeminiEmbedder uses the same langchain-google-genai SDK installed for the LLM
and the same error classification as pipeline/llm/client.py.
Batching is the caller's job (services/indexing.py, INDEX_BATCH_SIZE).
The deterministic test double lives in tests/fakes.py (FakeEmbedder).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Protocol, runtime_checkable

from app.pipeline.config import get_config
from app.pipeline.llm.client import _is_config_error, _is_transient


@runtime_checkable
class Embedder(Protocol):
    def embed(self, texts: list[str], *, task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
        ...


class GeminiEmbedder:
    """Gemini embeddings via langchain-google-genai.

    One client per task type, created lazily and reused.  Transient errors
    (429 per-minute, 5xx, timeouts) are retried with backoff; config errors
    and anything else propagate immediately.
    """

    _BACKOFF_S = (1.0, 2.0, 4.0)

    def __init__(self, model: str | None = None, dim: int | None = None) -> None:
        cfg = get_config()
        self._model = model or cfg.EMBED_MODEL
        self._dim = dim or cfg.EMBED_DIM
        self._clients: dict[str, Any] = {}
        self._lock = threading.Lock()

    def _client(self, task_type: str) -> Any:
        with self._lock:
            if task_type not in self._clients:
                from langchain_google_genai import GoogleGenerativeAIEmbeddings

                self._clients[task_type] = GoogleGenerativeAIEmbeddings(
                    model=self._model,
                    task_type=task_type,
                    output_dimensionality=self._dim,
                )
            return self._clients[task_type]

    def embed(self, texts: list[str], *, task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
        client = self._client(task_type)
        for attempt in range(len(self._BACKOFF_S) + 1):
            try:
                return client.embed_documents(texts)
            except Exception as exc:
                if _is_config_error(exc) or not _is_transient(exc) or attempt == len(self._BACKOFF_S):
                    raise
                time.sleep(self._BACKOFF_S[attempt])
        raise AssertionError("unreachable")
