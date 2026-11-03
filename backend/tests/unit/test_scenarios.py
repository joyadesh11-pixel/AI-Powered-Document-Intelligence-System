"""Dry-run scenario tests.

Scenarios that require LLM calls use explicit FakeLLM; scenarios that don't
(corrupt, unsupported, exception-in-segment) stay as parametrized tests.
Sheet scenarios 6–8 and 13 were already using FakeLLM and are unchanged.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.pipeline.graph import process, _default_deps
from app.pipeline.state import Deps
from app.pipeline.config import get_config
from app.schemas.models import (
    DocStatus, ExtractedInvoice, IssueCode, LineItemLLM,
    SheetMapping, VisionExtraction,
)
from tests.fakes import FakeLLM


# ------------------------------------------------------------------ helpers

def _gt_extracted(gt: dict, filename: str, idx: int = 0) -> ExtractedInvoice:
    """Build ExtractedInvoice from ground_truth.json printed fields."""
    entry = (gt.get(filename) or {})
    invs = entry.get("invoices", [])
    if idx >= len(invs):
        return ExtractedInvoice()
    inv = invs[idx]
    p = inv.get("printed") or {}
    line_items = [
        LineItemLLM(
            description=li.get("description"),
            quantity=str(li["quantity"]) if li.get("quantity") is not None else None,
            unit_price=str(li["unit_price"]) if li.get("unit_price") is not None else None,
            amount=str(li["amount"]) if li.get("amount") is not None else None,
        )
        for li in p.get("line_items", [])
    ]
    return ExtractedInvoice(
        invoice_number=p.get("invoice_number"),
        invoice_date=p.get("invoice_date"),
        due_date=p.get("due_date"),
        supplier_name=p.get("supplier_name"),
        customer_name=p.get("customer_name"),
        subtotal=p.get("subtotal"),
        tax=p.get("tax"),
        total=p.get("total"),
        currency=p.get("currency"),
        line_items=line_items,
    )


def _bad_total(good: ExtractedInvoice) -> ExtractedInvoice:
    """Return a copy of good with total='999.00' (arithmetic error)."""
    return ExtractedInvoice(**{**good.model_dump(), "total": "999.00"})


def _make_deps(llm) -> Deps:
    return Deps(config=get_config(), llm=llm, seen_store=None, mapping_cache=None)


# ---- Scenarios that never reach the LLM node (remain parametrized) ----

# NOTE: sim no longer includes gt_invoices; real LLM nodes use FakeLLM or real client.
SCENARIOS = [
    ("corrupt file",               "corrupt",    {}, DocStatus.failed,       None),
    ("unsupported .docx",          "unsupported",{}, DocStatus.failed,       None),
    ("exception inside segment",   "pdf",        {"raise_in": "segment"},
     DocStatus.failed, None),
]


@pytest.mark.parametrize(
    "label,file_key,sim,expected_status,assert_no_node",
    SCENARIOS,
    ids=[s[0] for s in SCENARIOS],
)
def test_scenario(label, file_key, sim, expected_status, assert_no_node, fx, gt):
    file_path = str(fx[file_key])
    result = process(file_path, deps=_default_deps(), _sim=sim)
    assert result.status == expected_status, (
        f"[{label}] expected {expected_status!r}, got {result.status!r}\n"
        f"history: {[h['node'] for h in result.history]}"
    )
    if assert_no_node:
        visited = [h["node"] for h in result.history]
        assert assert_no_node not in visited, (
            f"[{label}] expected {assert_no_node!r} NOT to run, "
            f"but history was: {visited}"
        )


# ---- Scenario 1: digital PDF, clean ----

def test_scenario_1_digital_clean(fx, gt):
    """Scenario 1: digital PDF → FakeLLM from GT → validated_ok."""
    file_path = str(fx["pdf"])
    filename = Path(file_path).name
    good = _gt_extracted(gt, filename, 0)
    # If GT has no data, use a minimal valid invoice
    if good.total is None:
        good = ExtractedInvoice(
            invoice_number="DIG-001",
            invoice_date="2026-01-01",
            supplier_name="DigCo",
            customer_name="Client",
            subtotal="100.00",
            tax="10.00",
            total="110.00",
            currency="USD",
        )

    fake = FakeLLM([good])
    result = process(file_path, deps=_make_deps(fake))
    assert result.status == DocStatus.validated_ok, (
        f"[scenario 1] expected validated_ok, got {result.status!r}\n"
        f"history: {[h['node'] for h in result.history]}\n"
        f"warnings: {[getattr(w, 'code', w) for w in result.warnings]}"
    )


# ---- Scenario 2: scanned image ----

def test_scenario_2_scanned_image(fx, gt):
    """Scenario 2: scan_clean.png → real OCR extracts text → LLM extracts invoice → validated_ok."""
    file_path = str(fx["scan_clean"])
    fake = FakeLLM([
        ExtractedInvoice(
            invoice_number="SCAN-0001",
            invoice_date="2026-11-01",
            supplier_name="ScanCo Ltd",
            customer_name="TestClient Corp",
            subtotal="90.00",
            tax="9.00",
            total="99.00",
            currency="USD",
            line_items=[
                LineItemLLM(description="Widget", quantity="10",
                            unit_price="5.00", amount="50.00"),
                LineItemLLM(description="Gadget", quantity="4",
                            unit_price="10.00", amount="40.00"),
            ],
        )
    ])
    deps = _make_deps(fake)
    result = process(file_path, deps=deps)
    assert result.status == DocStatus.validated_ok, (
        f"[scenario 2] expected validated_ok, got {result.status!r}\n"
        f"history: {[h['node'] for h in result.history]}\n"
        f"warnings: {[getattr(w, 'code', w) for w in result.warnings]}"
    )


# ---- Scenario 3: mixed PDF + low-conf OCR ----

def test_scenario_3_mixed_low_conf(fx, gt):
    """Scenario 3: mixed.pdf — digital page 0 extracted by text LLM; low-conf page 1 by vision."""
    file_path = str(fx["mixed"])
    filename = Path(file_path).name
    good = _gt_extracted(gt, filename, 0)
    if good.total is None:
        good = ExtractedInvoice(
            invoice_number="MIX-001", invoice_date="2026-01-01",
            supplier_name="MixCo", customer_name="Client",
            subtotal="100.00", tax="10.00", total="110.00", currency="USD",
        )
    good_vision = VisionExtraction(**good.model_dump(), source_text="Mixed PDF invoice text")
    # Provide enough responses for text + vision extraction; segment may produce 1 or 2 units
    fake = FakeLLM([good, good, good_vision, good_vision])
    sim = {"low_conf_units": [1]}  # compat shim (ocr.py reads this for M7 removal)
    result = process(file_path, deps=_make_deps(fake), _sim=sim)
    assert result.status in (DocStatus.validated_ok, DocStatus.needs_review), (
        f"[scenario 3] expected validated_ok or needs_review, got {result.status!r}\n"
        f"history: {[h['node'] for h in result.history]}\n"
        f"warnings: {[getattr(w, 'code', w) for w in result.warnings]}"
    )


# ---- Scenario 4: arith error fixed by vision ----

def test_scenario_4_arith_fixed_by_vision(fx, gt):
    """Scenario 4: text LLM returns wrong total → vision corrects it → validated_ok."""
    file_path = str(fx["pdf"])
    filename = Path(file_path).name
    good = _gt_extracted(gt, filename, 0)
    if good.total is None:
        good = ExtractedInvoice(
            invoice_number="DIG-001", invoice_date="2026-01-01",
            supplier_name="DigCo", customer_name="Client",
            subtotal="100.00", tax="10.00", total="110.00", currency="USD",
        )
    bad = _bad_total(good)
    good_vision = VisionExtraction(**good.model_dump(), source_text="PDF invoice text")
    # text extraction returns bad total; vision returns correct
    fake = FakeLLM([bad, good_vision])
    result = process(file_path, deps=_make_deps(fake))
    assert result.status == DocStatus.validated_ok, (
        f"[scenario 4] expected validated_ok, got {result.status!r}\n"
        f"history: {[h['node'] for h in result.history]}\n"
        f"warnings: {[getattr(w, 'code', w) for w in result.warnings]}"
    )


# ---- Scenario 5: arith error persists ----

def test_scenario_5_arith_persists(fx, gt):
    """Scenario 5: both text and vision return wrong total → needs_review."""
    file_path = str(fx["pdf"])
    filename = Path(file_path).name
    good = _gt_extracted(gt, filename, 0)
    if good.total is None:
        good = ExtractedInvoice(
            invoice_number="DIG-001", invoice_date="2026-01-01",
            supplier_name="DigCo", customer_name="Client",
            subtotal="100.00", tax="10.00", total="110.00", currency="USD",
        )
    bad = _bad_total(good)
    bad_vision = VisionExtraction(**bad.model_dump(), source_text="PDF invoice text")
    fake = FakeLLM([bad, bad_vision])
    result = process(file_path, deps=_make_deps(fake))
    assert result.status == DocStatus.needs_review, (
        f"[scenario 5] expected needs_review, got {result.status!r}\n"
        f"history: {[h['node'] for h in result.history]}\n"
        f"warnings: {[getattr(w, 'code', w) for w in result.warnings]}"
    )


# ---- Scenario 6: xlsx tabular, standard headers → 0 LLM calls ----

def _std_linegrain() -> SheetMapping:
    return SheetMapping(
        grain="line_item",
        group_by="Invoice Number",
        invoice_fields={
            "invoice_number": "Invoice Number",
            "invoice_date": "Invoice Date",
            "supplier_name": "Supplier",
            "customer_name": "Customer",
            "currency": "Currency",
            "subtotal": "Subtotal",
            "tax": "Tax",
            "total": "Total",
        },
        line_fields={
            "description": "Description",
            "quantity": "Quantity",
            "unit_price": "Unit Price",
            "amount": "Amount",
        },
        date_format="%Y-%m-%d",
        decimal_separator=".",
        thousands_separator=",",
    )


def test_scenario_6_xlsx_tabular_ok(fx, gt):
    """Scenario 6: standard-header xlsx → validated_ok, 0 LLM calls."""
    file_path = str(fx["xlsx_simple_linegrain"])
    fake = FakeLLM([])
    deps = Deps(config=None, llm=fake, seen_store=None, mapping_cache=None)
    result = process(file_path, deps=deps)
    assert result.status == DocStatus.validated_ok, (
        f"[scenario 6] expected validated_ok, got {result.status!r}\n"
        f"warnings: {[w.code for w in result.warnings]}\n"
        f"history: {[h['node'] for h in result.history]}"
    )
    assert len(fake.calls) == 0, (
        f"[scenario 6] expected 0 LLM calls, got {len(fake.calls)}"
    )


# ---- Scenario 7: xlsx_odd_headers — mapping bad once (retry) → validated_ok ----

def _odd_headers_correct() -> SheetMapping:
    return SheetMapping(
        grain="line_item",
        group_by="Doc Ref",
        invoice_fields={
            "invoice_number": "Doc Ref",
            "invoice_date": "Issued On",
            "supplier_name": "Supplier Co",
            "customer_name": "Customer",
            "currency": "Currency",
            "total": "Invoice Total",
        },
        line_fields={
            "description": "Description",
            "quantity": "Quantity",
            "unit_price": "Unit Price",
            "amount": "Line Amt",
        },
        date_format="%d/%m/%Y",
        decimal_separator=".",
        thousands_separator=",",
    )


def _odd_headers_wrong_date() -> SheetMapping:
    m = _odd_headers_correct()
    return SheetMapping(**{**m.model_dump(), "date_format": "%m/%d/%Y"})


def test_scenario_7_mapping_bad_once(fx, gt):
    """Scenario 7: odd-headers xlsx — first LLM pass wrong date, second correct → validated_ok, 2 calls."""
    file_path = str(fx["xlsx_odd_headers"])
    fake = FakeLLM([_odd_headers_wrong_date(), _odd_headers_correct()])
    deps = Deps(config=None, llm=fake, seen_store=None, mapping_cache=None)
    result = process(file_path, deps=deps)
    assert result.status == DocStatus.validated_ok, (
        f"[scenario 7] expected validated_ok, got {result.status!r}\n"
        f"warnings: {[w.code for w in result.warnings]}\n"
        f"history: {[h['node'] for h in result.history]}"
    )
    assert len(fake.calls) == 2, (
        f"[scenario 7] expected 2 LLM calls, got {len(fake.calls)}"
    )


# ---- Scenario 8: xlsx_odd_headers — mapping bad twice → needs_review + MAPPING_FAILED ----

def test_scenario_8_mapping_bad_twice(fx, gt):
    """Scenario 8: both LLM responses give wrong date format → needs_review + MAPPING_FAILED."""
    file_path = str(fx["xlsx_odd_headers"])
    wrong = _odd_headers_wrong_date()
    fake = FakeLLM([wrong, wrong])
    deps = Deps(config=None, llm=fake, seen_store=None, mapping_cache=None)
    result = process(file_path, deps=deps)
    assert result.status == DocStatus.needs_review, (
        f"[scenario 8] expected needs_review, got {result.status!r}\n"
        f"history: {[h['node'] for h in result.history]}"
    )
    codes = [w.code for w in result.warnings]
    assert IssueCode.MAPPING_FAILED in codes, (
        f"[scenario 8] expected MAPPING_FAILED in warnings, got {codes}"
    )


# ---- Full-graph ghost-column exhaustion ----

def _odd_headers_ghost_col() -> SheetMapping:
    m = _odd_headers_correct()
    fields = dict(m.invoice_fields)
    fields["invoice_number"] = "GHOST_COL"
    return SheetMapping(**{**m.model_dump(), "invoice_fields": fields})


def test_graph_ghost_column_exhaustion_three_llm_calls(fx, gt):
    """Full-graph: all 3 attempts return ghost column → MAPPING_FAILED, 0 invoices."""
    cfg = get_config()
    n_attempts = cfg.LLM_MAX_SCHEMA_RETRIES + 1
    file_path = str(fx["xlsx_odd_headers"])
    bad = _odd_headers_ghost_col()
    fake = FakeLLM([bad] * n_attempts)
    deps = Deps(config=None, llm=fake, seen_store=None, mapping_cache=None)
    result = process(file_path, deps=deps)
    assert len(fake.calls) == n_attempts
    assert result.status == DocStatus.needs_review
    codes = [w.code for w in result.warnings]
    assert IssueCode.MAPPING_FAILED in codes
    assert len(result.invoices) == 0


# ---- Scenario 13: xlsx arith error → needs_review, no vision calls ----

def test_scenario_13_xlsx_arith_error_no_vision(fx, gt):
    """Scenario 13: xlsx with arithmetic error → needs_review; vision_extract never runs."""
    file_path = str(fx["xlsx_arith_error"])
    fake = FakeLLM([])
    deps = Deps(config=None, llm=fake, seen_store=None, mapping_cache=None)
    result = process(file_path, deps=deps)
    assert result.status == DocStatus.needs_review, (
        f"[scenario 13] expected needs_review, got {result.status!r}\n"
        f"warnings: {[w.code for w in result.warnings]}\n"
        f"history: {[h['node'] for h in result.history]}"
    )
    visited = [h["node"] for h in result.history]
    assert "vision_extract" not in visited
    assert len(fake.calls) == 0
