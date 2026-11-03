"""Embedder protocol, fake implementation for tests, and Gemini implementation.

EMBED_MODEL and EMBED_DIM come exclusively from config — never hardcoded here.
GeminiEmbedder uses the same langchain-google-genai SDK installed for the LLM.
FakeEmbedder produces deterministic, text-dependent vectors suitable for ranking tests.
"""
from __future__ import annotations

import hashlib
import math
from typing import Protocol, runtime_checkable

from app.pipeline.config import get_config


@runtime_checkable
class Embedder(Protocol):
    def embed(self, texts: list[str], *, task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
        ...


class FakeEmbedder:
    """Deterministic bag-of-words embedder for tests.

    Texts that share words produce higher cosine similarity — so a query for
    "Acme invoice" ranks an "Acme Corp Invoice #123" chunk above "Foo Bar Widget".
    """

    DIM = 768

    def embed(self, texts: list[str], *, task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
        results = []
        for text in texts:
            vec = [0.0] * self.DIM
            for word in text.lower().split():
                h = hashlib.sha256(word.encode()).digest()
                for i in range(0, 32, 4):
                    idx = int.from_bytes(h[i:i + 4], "little") % self.DIM
                    vec[idx] += 1.0
            magnitude = math.sqrt(sum(x * x for x in vec)) or 1.0
            results.append([x / magnitude for x in vec])
        return results


class GeminiEmbedder:
    """Gemini embedding via langchain-google-genai.

    Batches inputs in groups of 32 (API limit).
    Retries once on transient errors (429 / 5xx) before propagating.
    """

    def __init__(self, model: str | None = None, dim: int | None = None) -> None:
        cfg = get_config()
        self._model = model or cfg.EMBED_MODEL
        self._dim = dim or cfg.EMBED_DIM

    def embed(self, texts: list[str], *, task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        embeddings_client = GoogleGenerativeAIEmbeddings(
            model=self._model,
            task_type=task_type,
            output_dimensionality=self._dim,
        )

        results: list[list[float]] = []
        batch_size = 32
        for i in range(0, len(texts), batch_size):
            batch = texts[i: i + batch_size]
            try:
                vecs = embeddings_client.embed_documents(batch)
            except Exception:
                # One retry on transient errors
                vecs = embeddings_client.embed_documents(batch)
            results.extend(vecs)
        return results
