"""Tests for RealLLMClient retry logic.

All tests here monkeypatch the LangChain chain — no real API calls.
The 'non-live test must not construct RealLLMClient' guard is also here.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.pipeline.config import get_config
from app.pipeline.llm.client import LLMConfigError, RealLLMClient
from app.schemas.models import ExtractedInvoice


def _raw_msg(input_tokens: int = 10, output_tokens: int = 5) -> MagicMock:
    msg = MagicMock()
    msg.usage_metadata = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    return msg


def _success(parsed):
    return {"raw": _raw_msg(), "parsed": parsed, "parsing_error": None}


def _parse_fail(err):
    return {"raw": _raw_msg(), "parsed": None, "parsing_error": err}


def _make_chain(side_effects: list):
    chain = MagicMock()
    chain.invoke.side_effect = side_effects
    return chain


def _make_llm(chain):
    llm = MagicMock()
    llm.with_structured_output.return_value = chain
    return llm


def _client() -> RealLLMClient:
    return RealLLMClient(get_config())


# ------------------------------------------------------------------ T1: schema retry

def test_schema_retry_second_prompt_contains_error(monkeypatch):
    """Schema error then success: second chain.invoke call gets error text appended."""
    err = ValueError("missing required field 'total'")
    good = ExtractedInvoice(invoice_number="INV-001", total="110.00")

    invoke_args: list[list] = []

    def capturing_invoke(messages):
        invoke_args.append(messages)
        if len(invoke_args) == 1:
            return _parse_fail(err)
        return _success(good)

    chain = MagicMock()
    chain.invoke.side_effect = capturing_invoke
    llm = _make_llm(chain)

    with patch("app.pipeline.llm.client.ChatGoogleGenerativeAI", return_value=llm):
        client = _client()
        result, usage = client.extract(ExtractedInvoice, "system", "user text", model="test")

    assert chain.invoke.call_count == 2
    assert result.invoice_number == "INV-001"
    # Second call must contain the error text
    second_user_content = invoke_args[1][-1].content  # HumanMessage content
    second_user_str = str(second_user_content)
    assert "missing required field" in second_user_str or "missing required field" in second_user_str


# ------------------------------------------------------------------ T2: transient retry (429)

def test_two_transient_errors_then_success_sleeps_twice(monkeypatch):
    """Two per-minute 429s then success: time.sleep called exactly twice."""
    sleep_calls: list[float] = []
    monkeypatch.setattr("app.pipeline.llm.client.time.sleep", lambda s: sleep_calls.append(s))

    class FakeRateLimit(Exception):
        pass

    monkeypatch.setattr(
        "app.pipeline.llm.client._is_transient",
        lambda exc: isinstance(exc, FakeRateLimit),
    )
    monkeypatch.setattr("app.pipeline.llm.client._is_config_error", lambda exc: False)

    good = ExtractedInvoice(invoice_number="INV-002")
    chain = _make_chain([
        FakeRateLimit("429 per minute"),
        FakeRateLimit("429 per minute"),
        _success(good),
    ])
    llm = _make_llm(chain)

    with patch("app.pipeline.llm.client.ChatGoogleGenerativeAI", return_value=llm):
        result, usage = _client().extract(ExtractedInvoice, "sys", "user", model="m")

    assert len(sleep_calls) == 2
    assert result.invoice_number == "INV-002"


# ------------------------------------------------------------------ T3: 401/403 → LLMConfigError

def test_auth_error_raises_llm_config_error_no_retry(monkeypatch):
    """401 → LLMConfigError raised immediately; chain.invoke called exactly once."""

    class FakeAuthError(Exception):
        pass

    monkeypatch.setattr(
        "app.pipeline.llm.client._is_config_error",
        lambda exc: isinstance(exc, FakeAuthError),
    )
    monkeypatch.setattr("app.pipeline.llm.client._is_transient", lambda exc: False)

    chain = _make_chain([FakeAuthError("invalid API key")])
    llm = _make_llm(chain)

    with patch("app.pipeline.llm.client.ChatGoogleGenerativeAI", return_value=llm):
        with pytest.raises(LLMConfigError):
            _client().extract(ExtractedInvoice, "sys", "user", model="m")

    assert chain.invoke.call_count == 1


def test_permission_denied_raises_llm_config_error_no_retry(monkeypatch):
    """403 → LLMConfigError raised immediately; no retry."""

    class FakePermDenied(Exception):
        pass

    monkeypatch.setattr(
        "app.pipeline.llm.client._is_config_error",
        lambda exc: isinstance(exc, FakePermDenied),
    )
    monkeypatch.setattr("app.pipeline.llm.client._is_transient", lambda exc: False)

    chain = _make_chain([FakePermDenied("permission denied")])
    llm = _make_llm(chain)

    with patch("app.pipeline.llm.client.ChatGoogleGenerativeAI", return_value=llm):
        with pytest.raises(LLMConfigError):
            _client().extract(ExtractedInvoice, "sys", "user", model="m")

    assert chain.invoke.call_count == 1


# ------------------------------------------------------------------ T4: include_raw=True shape

def test_include_raw_parse_error_shape_triggers_retry():
    """`with_structured_output(include_raw=True)` returns {"parsed": None, "parsing_error": e}.
    The client detects this and enters the schema-retry loop (eventually succeeds)."""
    err = ValueError("schema parse error")
    good = ExtractedInvoice(invoice_number="INV-003")

    responses = [
        _parse_fail(err),
        _success(good),
    ]
    chain = _make_chain(responses)
    llm = _make_llm(chain)

    with patch("app.pipeline.llm.client.ChatGoogleGenerativeAI", return_value=llm):
        result, usage = _client().extract(ExtractedInvoice, "sys", "user", model="m")

    assert chain.invoke.call_count == 2
    assert result.invoice_number == "INV-003"
    assert usage.calls >= 2


def test_include_raw_parsed_none_no_parsing_error_also_retries():
    """If parsed is None and parsing_error is also None, client still retries."""
    good = ExtractedInvoice(invoice_number="INV-004")

    responses = [
        {"raw": _raw_msg(), "parsed": None, "parsing_error": None},
        _success(good),
    ]
    chain = _make_chain(responses)
    llm = _make_llm(chain)

    with patch("app.pipeline.llm.client.ChatGoogleGenerativeAI", return_value=llm):
        result, _ = _client().extract(ExtractedInvoice, "sys", "user", model="m")

    assert result.invoice_number == "INV-004"


# ------------------------------------------------------------------ T5: non-live test guard

def test_default_deps_always_null_llm():
    """_default_deps() must return NullLLM regardless of environment.

    Fails if GOOGLE_API_KEY in environment would cause _default_deps to build
    a RealLLMClient — which would make non-live tests spend real API quota.
    """
    from app.pipeline.graph import _default_deps, NullLLM
    deps = _default_deps()
    assert isinstance(deps["llm"], NullLLM), (
        "_default_deps() returned a non-NullLLM. "
        "This would spend API quota when running offline tests."
    )
    assert not isinstance(deps["llm"], RealLLMClient), (
        "_default_deps() returned RealLLMClient — must use NullLLM."
    )


# ------------------------------------------------------------------ T6: usage accumulation

def test_usage_accumulates_across_retries():
    """Usage tokens are summed across all schema retry attempts."""
    err = ValueError("bad parse")
    good = ExtractedInvoice(invoice_number="INV-005")

    responses = [
        {"raw": MagicMock(usage_metadata={"input_tokens": 100, "output_tokens": 50}),
         "parsed": None, "parsing_error": err},
        {"raw": MagicMock(usage_metadata={"input_tokens": 80, "output_tokens": 40}),
         "parsed": good, "parsing_error": None},
    ]
    chain = _make_chain(responses)
    llm = _make_llm(chain)

    with patch("app.pipeline.llm.client.ChatGoogleGenerativeAI", return_value=llm):
        _, usage = _client().extract(ExtractedInvoice, "sys", "user", model="m")

    assert usage.calls == 2
    assert usage.input_tokens == 180
    assert usage.output_tokens == 90


# ------------------------------------------------------------------ T7: daily quota 429

def test_daily_quota_exhausted_raises_llm_config_error(monkeypatch):
    """Per-day quota 429 (message contains 'per day') → LLMConfigError, not retry."""

    class FakeRateLimitDaily(Exception):
        pass

    # Force _is_config_error to return True for this daily-quota exception
    monkeypatch.setattr(
        "app.pipeline.llm.client._is_config_error",
        lambda exc: isinstance(exc, FakeRateLimitDaily),
    )
    monkeypatch.setattr("app.pipeline.llm.client._is_transient", lambda exc: False)

    chain = _make_chain([FakeRateLimitDaily("RESOURCE_EXHAUSTED: quota per day exceeded")])
    llm = _make_llm(chain)

    with patch("app.pipeline.llm.client.ChatGoogleGenerativeAI", return_value=llm):
        with pytest.raises(LLMConfigError):
            _client().extract(ExtractedInvoice, "sys", "user", model="m")

    assert chain.invoke.call_count == 1
