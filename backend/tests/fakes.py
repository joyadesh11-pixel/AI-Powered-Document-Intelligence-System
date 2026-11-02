"""Test fakes: FakeLLM for injecting canned responses without network calls."""
from __future__ import annotations

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
