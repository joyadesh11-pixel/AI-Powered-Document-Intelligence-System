"""LLM client protocol and stub real implementation.

Tests inject FakeLLM (see tests/fakes.py). Real implementation filled in M6.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            calls=self.calls + other.calls,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class LLMConfigError(Exception):
    """Raised for unrecoverable LLM config problems (bad key, quota, wrong model).
    Documents fail with LLM_CONFIG_ERROR; no retry."""


@runtime_checkable
class LLMClient(Protocol):
    def extract(
        self,
        schema: type[T],
        system: str,
        user: str,
        *,
        images: list[bytes] | None = None,
        model: str,
    ) -> tuple[T, Usage]:
        ...


class RealLLMClient:
    """LangChain-backed Gemini client. Filled in M6."""

    def extract(
        self,
        schema: type[T],
        system: str,
        user: str,
        *,
        images: list[bytes] | None = None,
        model: str,
    ) -> tuple[T, Usage]:
        raise NotImplementedError("RealLLMClient implemented in M6")
