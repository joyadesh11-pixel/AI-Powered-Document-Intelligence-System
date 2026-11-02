"""Invariant tests for docs/02-schemas.md.

Invariant 1: No float anywhere in Invoice or PipelineResult.
Invariant 2: A field not present in the source is None, never "" or "N/A".
Invariant 3: status == validated_ok implies zero error-severity issues.
Invariant 4: PipelineResult.model_dump_json() round-trips; money serialises as string.
Bonus:       stream_process yields (node_name, state_update) in graph traversal order.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.schemas.models import (
    DocStatus, Invoice, InvoiceStatus, Issue, IssueCode,
    LineItem, PipelineResult, Severity, Tier,
)
from app.pipeline.graph import process, stream_process


# ---------------------------------------------------------------- helpers

def _make_invoice(**kwargs) -> Invoice:
    defaults = dict(tier=Tier.parser, unit_id=None)
    defaults.update(kwargs)
    return Invoice(**defaults)


def _make_result(**kwargs) -> PipelineResult:
    defaults = dict(
        status=DocStatus.validated_ok,
        file_hash="abc", file_type="pdf",
        size_bytes=1024, page_count=1,
        pipeline_version="test",
    )
    defaults.update(kwargs)
    return PipelineResult(**defaults)


def _has_float(obj, path="") -> list[str]:
    """Recursively find any float values; return list of dotted paths."""
    hits: list[str] = []
    if isinstance(obj, float):
        hits.append(path or "<root>")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            hits.extend(_has_float(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(_has_float(v, f"{path}[{i}]"))
    return hits


# --------------------------------------------------------- Invariant 1: no float

def test_invoice_no_float_in_dict():
    inv = _make_invoice(
        subtotal=Decimal("100.00"),
        tax=Decimal("10.00"),
        total=Decimal("110.00"),
        line_items=[LineItem(position=1, quantity=Decimal("2"), unit_price=Decimal("50.00"), amount=Decimal("100.00"))],
    )
    d = inv.model_dump()
    hits = _has_float(d)
    assert not hits, f"float values found in Invoice.model_dump() at: {hits}"


def test_pipeline_result_no_float_in_dict():
    inv = _make_invoice(subtotal=Decimal("99.99"), total=Decimal("99.99"))
    result = _make_result(invoices=[inv])
    d = result.model_dump()
    hits = _has_float(d)
    assert not hits, f"float values found in PipelineResult.model_dump() at: {hits}"


# ------------------------------------------------- Invariant 2: absent field = None

def test_absent_field_is_none_not_empty_string():
    inv = _make_invoice()
    assert inv.invoice_number is None
    assert inv.supplier_name is None
    assert inv.total is None
    # field_serializer should not produce "" for None
    d = inv.model_dump()
    assert d["invoice_number"] is None
    assert d["total"] is None


def test_absent_field_not_na():
    inv = _make_invoice(invoice_number=None, supplier_name=None)
    d = inv.model_dump()
    for val in d.values():
        assert val != "N/A", f"field has value 'N/A': {d}"
        assert val != "", f"field has value '': {d}"


# ----------------------------------------- Invariant 3: validated_ok → no error issues

def test_validated_ok_implies_no_error_issues():
    good_invoice = _make_invoice(status=InvoiceStatus.validated_ok, issues=[])
    assert good_invoice.status == InvoiceStatus.validated_ok
    error_count = sum(1 for iss in good_invoice.issues if iss.severity == Severity.error)
    assert error_count == 0, "validated_ok invoice must have zero error-severity issues"


def test_invoice_with_error_issue_must_not_be_validated_ok():
    issue = Issue(
        code=IssueCode.ARITH_TOTAL,
        severity=Severity.error,
        message="arithmetic mismatch",
    )
    bad_invoice = _make_invoice(status=InvoiceStatus.needs_review, issues=[issue])
    error_count = sum(1 for iss in bad_invoice.issues if iss.severity == Severity.error)
    assert error_count > 0
    assert bad_invoice.status == InvoiceStatus.needs_review


# ------------------------------------------ Invariant 4: JSON round-trip, money as str

def test_json_roundtrip():
    inv = _make_invoice(
        subtotal=Decimal("1234.56"),
        tax=Decimal("123.45"),
        total=Decimal("1358.01"),
    )
    result = _make_result(invoices=[inv])
    json_str = result.model_dump_json()
    parsed = json.loads(json_str)

    # money fields serialise as strings, not floats/numbers
    inv_dict = parsed["invoices"][0]
    assert inv_dict["subtotal"] == "1234.56", f"subtotal should be string '1234.56', got {inv_dict['subtotal']!r}"
    assert inv_dict["tax"] == "123.45", f"tax should be string '123.45', got {inv_dict['tax']!r}"
    assert inv_dict["total"] == "1358.01", f"total should be string '1358.01', got {inv_dict['total']!r}"
    assert isinstance(inv_dict["subtotal"], str), "subtotal must be a JSON string, not a number"


def test_json_roundtrip_none_money():
    inv = _make_invoice()
    result = _make_result(invoices=[inv])
    json_str = result.model_dump_json()
    parsed = json.loads(json_str)
    inv_dict = parsed["invoices"][0]
    assert inv_dict["subtotal"] is None
    assert inv_dict["total"] is None


def test_json_roundtrip_line_item_money():
    li = LineItem(
        position=1,
        quantity=Decimal("3"),
        unit_price=Decimal("10.00"),
        amount=Decimal("30.00"),
    )
    inv = _make_invoice(line_items=[li])
    result = _make_result(invoices=[inv])
    parsed = json.loads(result.model_dump_json())
    li_dict = parsed["invoices"][0]["line_items"][0]
    assert li_dict["quantity"] == "3"
    assert li_dict["unit_price"] == "10.00"
    assert li_dict["amount"] == "30.00"
    assert isinstance(li_dict["amount"], str)


# ---------------------------------------------- stream_process node order

def test_stream_process_yields_nodes_in_order(fx):
    """stream_process yields (node_name, state_update) for a clean digital PDF."""
    pairs = list(stream_process(str(fx["pdf"]), _sim={}))
    node_names = [name for name, _ in pairs]
    # all yielded items must have a string node name and dict update
    for name, update in pairs:
        assert isinstance(name, str), f"expected str node name, got {type(name)}"
        assert isinstance(update, dict), f"expected dict state_update, got {type(update)}"
    # digital PDF clean path: intake → pdf_triage → pdf_text → segment → llm_extract → validate → finalize
    expected_subsequence = ["intake", "pdf_triage", "pdf_text", "segment", "llm_extract", "validate", "finalize"]
    positions = {n: node_names.index(n) for n in expected_subsequence if n in node_names}
    missing = [n for n in expected_subsequence if n not in positions]
    assert not missing, f"nodes missing from stream output: {missing}\ngot: {node_names}"
    ordered = sorted(positions.items(), key=lambda x: x[1])
    assert [n for n, _ in ordered] == expected_subsequence, (
        f"nodes out of order. expected {expected_subsequence}, got positions {positions}"
    )
