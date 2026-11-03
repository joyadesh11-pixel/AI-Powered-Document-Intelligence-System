"""LLM client protocol, real Gemini implementation, and provider factory.

Tests inject FakeLLM (see tests/fakes.py).
Production code calls build_llm_client(cfg) which returns a RealLLMClient.
_default_deps() always uses NullLLM; never constructs RealLLMClient automatically.
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from app.pipeline.config import Config

T = TypeVar("T")

# Module-level import so tests can patch app.pipeline.llm.client.ChatGoogleGenerativeAI
try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:  # pragma: no cover
    ChatGoogleGenerativeAI = None  # type: ignore[assignment,misc]


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
    """Raised for unrecoverable LLM config problems (bad key, daily quota, wrong model).
    Documents fail with LLM_CONFIG_ERROR; no retry.

    _propagate = True so the @node wrapper re-raises instead of catching.
    """
    _propagate = True


class LLMTransientError(Exception):
    """Raised when transient retries (per-minute 429, timeout, 5xx) are exhausted.
    Nodes catch this and emit EXTRACTION_FAILED — the document does NOT fail.
    The @node wrapper converts uncaught exceptions into INTERNAL_ERROR, so nodes
    must catch LLMTransientError explicitly if they don't already catch Exception.
    """


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


def _build_image_content(images: list[bytes]) -> list[dict]:
    """Encode JPEG image bytes as base64 data URI content parts."""
    parts = []
    for img_bytes in images:
        b64 = base64.b64encode(img_bytes).decode()
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
    return parts


def _is_config_error(exc: Exception) -> bool:
    """Return True for errors that should never be retried (auth, bad model, hard quota)."""
    try:
        from langchain_google_genai.chat_models import (
            GoogleAuthenticationError,
            GooglePermissionDeniedError,
            GoogleModelNotFoundError,
        )
        if isinstance(exc, (GoogleAuthenticationError, GooglePermissionDeniedError,
                             GoogleModelNotFoundError)):
            return True
    except ImportError:
        pass
    # Hard daily-quota 429: no retry hint, message says "quota" + "per day" / "daily"
    try:
        from langchain_google_genai.chat_models import GoogleRateLimitError
        if isinstance(exc, GoogleRateLimitError):
            msg = str(exc).lower()
            # Per-day / project quotas: treat as config error
            if "per day" in msg or "daily" in msg or "project" in msg:
                return True
    except ImportError:
        pass
    return False


def _is_transient(exc: Exception) -> bool:
    """Return True for errors that warrant exponential-backoff retry."""
    try:
        from langchain_google_genai.chat_models import GoogleRateLimitError, GoogleAPIError
        if isinstance(exc, (GoogleRateLimitError, GoogleAPIError)):
            return True
    except ImportError:
        pass
    try:
        import httpx
        if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
            return True
    except ImportError:
        pass
    try:
        import requests
        if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
            return True
    except ImportError:
        pass
    return False


def _get_retry_after(exc: Exception) -> float | None:
    """Extract Retry-After delay (seconds) from a rate-limit error, if present."""
    # google-genai ClientError may expose response headers via exc.response
    try:
        resp = getattr(exc, "response", None)
        if resp is not None:
            headers = getattr(resp, "headers", {}) or {}
            ra = headers.get("Retry-After") or headers.get("retry-after")
            if ra:
                return float(ra)
    except (ValueError, TypeError, AttributeError):
        pass
    # Check details dict for retryDelay
    try:
        details = getattr(exc, "details", None)
        if isinstance(details, dict):
            retry_info = details.get("error", {})
            if isinstance(retry_info, dict):
                for item in retry_info.get("details", []):
                    if isinstance(item, dict) and "retryDelay" in item:
                        delay_str = str(item["retryDelay"]).rstrip("s")
                        return float(delay_str)
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def _extract_usage(raw_message: Any) -> Usage:
    """Extract token counts from a LangChain AIMessage."""
    meta = getattr(raw_message, "usage_metadata", None)
    if meta is None:
        return Usage(calls=1)
    return Usage(
        calls=1,
        input_tokens=meta.get("input_tokens", 0) if isinstance(meta, dict)
                     else getattr(meta, "input_tokens", 0),
        output_tokens=meta.get("output_tokens", 0) if isinstance(meta, dict)
                      else getattr(meta, "output_tokens", 0),
    )


class RealLLMClient:
    """LangChain-backed Gemini client.

    All provider-specific imports and error mapping are confined to this class.
    Use build_llm_client(cfg) to construct; never use in non-live tests.
    """

    def __init__(self, cfg: "Config") -> None:
        self._cfg = cfg

    def extract(
        self,
        schema: type[T],
        system: str,
        user: str,
        *,
        images: list[bytes] | None = None,
        model: str,
    ) -> tuple[T, Usage]:
        from langchain_core.messages import HumanMessage, SystemMessage
        from pydantic import ValidationError

        cfg = self._cfg
        llm = ChatGoogleGenerativeAI(model=model, temperature=0)
        chain = llm.with_structured_output(schema, include_raw=True)

        user_text = user
        total_usage = Usage()
        last_exc: Exception | None = None

        for schema_attempt in range(cfg.LLM_MAX_SCHEMA_RETRIES + 1):
            content: list[Any] = [{"type": "text", "text": user_text}]
            if images:
                content.extend(_build_image_content(images))
            messages = [
                SystemMessage(content=system),
                HumanMessage(content=content),
            ]

            for transient_attempt in range(cfg.LLM_MAX_TRANSIENT_RETRIES + 1):
                try:
                    result = chain.invoke(messages)
                    raw = result.get("raw")
                    parsed = result.get("parsed")
                    parsing_error = result.get("parsing_error")

                    usage = _extract_usage(raw) if raw is not None else Usage(calls=1)
                    total_usage = total_usage + usage

                    if parsing_error is not None or parsed is None:
                        # Schema/parse failure: treat like ValidationError
                        last_exc = parsing_error or ValueError("parsed is None")
                        break  # exit transient loop → schema retry

                    return parsed, total_usage

                except Exception as exc:  # noqa: BLE001
                    if _is_config_error(exc):
                        raise LLMConfigError(str(exc)) from exc

                    if _is_transient(exc):
                        last_exc = exc
                        if transient_attempt < cfg.LLM_MAX_TRANSIENT_RETRIES:
                            delay = _get_retry_after(exc) or float(1 << transient_attempt)
                            time.sleep(delay)
                            continue
                        # Transient retries exhausted — raise LLMTransientError so nodes
                        # can turn it into EXTRACTION_FAILED without failing the document.
                        raise LLMTransientError(
                            f"Transient LLM error after {cfg.LLM_MAX_TRANSIENT_RETRIES} retries: {exc}"
                        ) from exc
                    # Unknown error — re-raise
                    raise

            # Schema retry: append error text
            if schema_attempt < cfg.LLM_MAX_SCHEMA_RETRIES and last_exc is not None:
                user_text = (
                    user_text
                    + f"\n\nPrevious response failed validation: {last_exc}\n"
                    "Return corrected JSON only."
                )

        # All schema retries exhausted
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("LLM extract exhausted all retries with no error captured")


def build_llm_client(cfg: "Config") -> RealLLMClient:
    """Factory for production use. Call from scripts, services, and live tests.

    Requires GOOGLE_API_KEY in environment (LLM_PROVIDER=gemini, the only provider now).
    Never call from non-live tests or _default_deps().
    """
    return RealLLMClient(cfg)
