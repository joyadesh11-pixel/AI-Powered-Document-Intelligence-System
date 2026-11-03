"""Tests for finalize node (T1-T6 from spec)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.pipeline.nodes.finalize import finalize
from app.schemas.models import (
    DocStatus, IssueCode, PipelineResult, Severity,
)


def _base_state(**overrides) -> dict:
    s = {
        "file_hash": "abc123",
        "file_type": "pdf",
        "size_bytes": 1024,
        "page_count": 1,
        "page_text": {},
        "page_source": {},
        "ocr_conf": {},
        "warnings": [],
        "validation_errors": [],
        "invoices": [],
        "history": [],
        "llm_usage": {},
        "error": None,
        "error_code": None,
    }
    s.update(overrides)
    return s


def _clean_invoice(uid: int = 0) -> dict:
    return {
        "unit_id": uid,
        "tier": "parser",
        "invoice_number": f"INV-{uid:03d}",
        "invoice_date": None,
        "due_date": None,
        "supplier_name": "Acme",
        "customer_name": "Customer",
        "subtotal": Decimal("100.00"),
        "tax": Decimal("10.00"),
        "total": Decimal("110.00"),
        "currency": "USD",
        "line_items": [],
        "source_pages": [uid],
        "raw": {"subtotal": "100.00", "tax": "10.00", "total": "110.00"},
        "status": "validated_ok",
        "issues": [],
        "alternatives": [],
    }


def _error_invoice(uid: int = 0) -> dict:
    inv = _clean_invoice(uid)
    inv["status"] = "needs_review"
    inv["issues"] = [{
        "code": IssueCode.ARITH_TOTAL.value,
        "severity": Severity.error.value,
        "message": "arith error",
        "field": "total", "expected": None, "got": None, "location": {},
    }]
    return inv


# ------------------------------------------------------------------ T1: status matrix

def test_status_error_state_returns_empty():
    """T1a: error in state → finalize returns {} (fail node handles it)."""
    s = _base_state(error="segment: crash")
    result = finalize(s)
    assert result == {} or "result" not in result


def test_status_clean_invoices_validated_ok():
    """T1b: clean invoices → status=validated_ok."""
    s = _base_state(invoices=[_clean_invoice()])
    result = finalize(s)
    assert result.get("status") == DocStatus.validated_ok.value


def test_status_error_invoice_needs_review():
    """T1c: one error-severity invoice → status=needs_review."""
    s = _base_state(invoices=[_error_invoice()])
    result = finalize(s)
    assert result.get("status") == DocStatus.needs_review.value


def test_status_no_invoices_needs_review_with_warning():
    """T1d: no invoices → needs_review + NO_INVOICE_DETECTED warning."""
    s = _base_state()
    result = finalize(s)
    assert result.get("status") == DocStatus.needs_review.value
    r = result.get("result", {})
    codes = [w.get("code") if isinstance(w, dict) else getattr(w, "code", None)
             for w in r.get("warnings", [])]
    assert IssueCode.NO_INVOICE_DETECTED.value in codes


def test_status_parse_ok_false_mapping_failed():
    """T1e: parse_ok=False → needs_review + MAPPING_FAILED."""
    s = _base_state(parse_ok=False)
    result = finalize(s)
    assert result.get("status") == DocStatus.needs_review.value
    r = result.get("result", {})
    codes = [w.get("code") if isinstance(w, dict) else getattr(w, "code", None)
             for w in r.get("warnings", [])]
    assert IssueCode.MAPPING_FAILED.value in codes


# ------------------------------------------------------------------ T2: PipelineResult validates

def test_pipeline_result_validates_and_round_trips():
    """T2: PipelineResult.model_validate(result) succeeds and .model_dump_json() round-trips."""
    s = _base_state(invoices=[_clean_invoice()])
    result = finalize(s)
    raw = result.get("result", {})
    pr = PipelineResult.model_validate(raw)
    json_str = pr.model_dump_json()
    pr2 = PipelineResult.model_validate_json(json_str)
    assert pr2.status == pr.status


# ------------------------------------------------------------------ T3: validated_ok implies 0 error issues

def test_validated_ok_has_zero_error_issues():
    """T3: validated_ok → zero error-severity issues in all invoices."""
    s = _base_state(invoices=[_clean_invoice()])
    result = finalize(s)
    r = result.get("result", {})
    assert result.get("status") == DocStatus.validated_ok.value
    for inv in r.get("invoices", []):
        issues = inv.get("issues", []) if isinstance(inv, dict) else getattr(inv, "issues", [])
        for iss in issues:
            sev = iss.get("severity") if isinstance(iss, dict) else getattr(iss, "severity", None)
            assert sev != Severity.error.value, f"Error issue found in validated_ok invoice: {iss}"


# ------------------------------------------------------------------ T4: no frames key

def test_result_does_not_contain_frames():
    """T4: result dict does not contain 'frames' key."""
    s = _base_state(invoices=[_clean_invoice()], frames={"Sheet1": None})
    result = finalize(s)
    r = result.get("result", {})
    assert "frames" not in r


# ------------------------------------------------------------------ T5: timings sum

def test_timings_sum_matches_total():
    """T5: timings['by_node'] values sum to timings['total'] within 1 ms."""
    history = [
        {"node": "intake", "seconds": 0.123, "ok": True, "attempt": 0, "meta": {}},
        {"node": "pdf_triage", "seconds": 0.045, "ok": True, "attempt": 0, "meta": {}},
        {"node": "segment", "seconds": 0.012, "ok": True, "attempt": 0, "meta": {}},
        {"node": "llm_extract", "seconds": 1.234, "ok": True, "attempt": 0, "meta": {}},
        {"node": "validate", "seconds": 0.008, "ok": True, "attempt": 0, "meta": {}},
        {"node": "finalize", "seconds": 0.002, "ok": True, "attempt": 0, "meta": {}},
    ]
    s = _base_state(invoices=[_clean_invoice()], history=history)
    result = finalize(s)
    r = result.get("result", {})
    timings = r.get("timings", {})
    by_node = timings.get("by_node", {})
    total = timings.get("total", 0.0)
    computed_sum = round(sum(by_node.values()), 5)
    assert abs(computed_sum - total) < 0.001, (
        f"by_node sum {computed_sum} != total {total}"
    )


# ------------------------------------------------------------------ T6: pipeline_version bumped

def test_pipeline_version_is_m6():
    """pipeline_version is current (0.1.0-m8a)."""
    s = _base_state(invoices=[_clean_invoice()])
    result = finalize(s)
    r = result.get("result", {})
    assert r.get("pipeline_version") == "0.1.0-m8a"
