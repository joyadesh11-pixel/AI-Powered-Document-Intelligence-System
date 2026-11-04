"""Factories for the external clients services need (LLM, embedder).

Tasks and API dependencies call these at use time, so tests can monkeypatch
them with FakeLLM / FakeEmbedder (tests/conftest.py `fake_providers`).
"""
from __future__ import annotations

from typing import Any

from app.pipeline.config import get_config


def get_llm() -> Any:
    from app.pipeline.llm.client import build_llm_client

    return build_llm_client(get_config())


def get_embedder() -> Any:
    from app.services.embedder import GeminiEmbedder

    return GeminiEmbedder()
