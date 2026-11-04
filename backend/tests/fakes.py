"""Test fakes: FakeLLM and FakeEmbedder for injecting canned behaviour without network calls."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, TypeVar

from app.pipeline.llm.client import LLMConfigError, Usage

T = TypeVar("T")


@dataclass
class FakeLLM:
    """LLMClient implementation for tests.

    responses: list of (schema_instance | Exception) to return/raise in order.
    After exhaustion, raises IndexError.
    """
    responses: list[Any] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    def extract(
        self,
        schema: type[T],
        system: str,
        user: str,
        *,
        images: list[bytes] | None = None,
        model: str,
    ) -> tuple[T, Usage]:
        self.calls.append({
            "schema": schema.__name__ if hasattr(schema, "__name__") else str(schema),
            "model": model,
            "has_images": images is not None,
            "system": system,
            "user": user,
        })
        if not self.responses:
            raise IndexError("FakeLLM response queue exhausted")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response, Usage(calls=1, input_tokens=0, output_tokens=0)


class FakeEmbedder:
    """Deterministic bag-of-words embedder (Embedder protocol).

    Texts that share words get higher cosine similarity, so a query for
    "Acme invoice" ranks an "Acme Corp Invoice #123" chunk above "Foo Bar Widget".
    Records the size of every embed() call in `calls`.
    """

    DIM = 768

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def embed(self, texts: list[str], *, task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
        self.calls.append({"n": len(texts), "task_type": task_type})
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


class FailingEmbedder(FakeEmbedder):
    """Raises on the given 0-based call numbers (default: every call)."""

    def __init__(self, fail_on: set[int] | None = None) -> None:
        super().__init__()
        self._fail_on = fail_on

    def embed(self, texts: list[str], *, task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
        call_no = len(self.calls)
        if self._fail_on is None or call_no in self._fail_on:
            self.calls.append({"n": len(texts), "task_type": task_type, "failed": True})
            raise RuntimeError("Embedding service unavailable")
        return super().embed(texts, task_type=task_type)
