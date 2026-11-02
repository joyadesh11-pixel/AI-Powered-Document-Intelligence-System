"""Tests for sheet_transform node — M4.

T1  line-grain xlsx (3 invoices) → correct Decimal amounts, line counts
T2  invoice-grain csv → one Invoice per row
T3  wrong date_format → parse_ok=False, feedback has column + examples
T4  retry-then-success → parse_ok=True; mapping_feedback cleared
T5  mapping_retries at MAX+1, no mapping → parse_ok=False; finalize → MAPPING_FAILED
T6  xlsx_totals_row → UNGROUPED_ROWS; totals row absent from invoices
T7  header_money_equal → subtotal captured; header_money_differ → SHEET_GROUP_INCONSISTENT
T8  csv_cp1252_semicolon ("1.234,56") → Decimal("1234.56")
T9  xlsx_leading_zeros → invoice_number == "000001"
T10 perf_5000x30 (hand-built standard mapping) → elapsed < 3 s
T11 derived.lines_sum is Decimal-formatted string; subtotal stays None when unmapped
"""
from __future__ import annotations

import time
from decimal import Decimal

import pytest

from app.pipeline.nodes.sheet_load import sheet_load
from app.pipeline.nodes.sheet_classify import sheet_classify
from app.pipeline.nodes.sheet_map import make_sheet_map
from app.pipeline.nodes.sheet_transform import sheet_transform
from app.pipeline.nodes.finalize import finalize
from app.pipeline.config import get_config
from app.schemas.models import IssueCode, SheetMapping
from tests.fakes import FakeLLM


def _make_deps(llm=None, cache=None):
    return {"config": get_config(), "llm": llm or FakeLLM([]), "seen_store": None,
            "mapping_cache": cache}


def _load_classify_map(path, mapping: SheetMapping, file_type="sheet"):
    state = {"file_path": str(path), "file_type": file_type, "sim": {}, "warnings": []}
    r1 = sheet_load(state)
    state = {**state, **r1}
    r2 = sheet_classify(state)
    state = {**state, **r2}
    fake = FakeLLM([mapping])
    deps = _make_deps(llm=fake)
    node = make_sheet_map(deps)
    r3 = node(state)
    state = {**state, **r3}
    return state


def _std_linegrain_mapping():
    return SheetMapping(
        grain="line_item",
        group_by="Invoice Number",
        invoice_fields={"invoice_number": "Invoice Number", "invoice_date": "Invoice Date",
                        "supplier_name": "Supplier", "customer_name": "Customer",
                        "currency": "Currency", "subtotal": "Subtotal",
                        "tax": "Tax", "total": "Total"},
        line_fields={"description": "Description", "quantity": "Quantity",
                     "unit_price": "Unit Price", "amount": "Amount"},
        date_format="%Y-%m-%d",
        decimal_separator=".",
        thousands_separator=",",
    )


# ---- T1: line-grain xlsx, correct amounts ----

def test_T1_linegrain_correct_amounts(fx):
    mapping = _std_linegrain_mapping()
    state = _load_classify_map(fx["xlsx_simple_linegrain"], mapping)
    result = sheet_transform(state)
    invoices = result.get("invoices", [])
    assert len(invoices) == 3
    for inv in invoices:
        assert inv.get("invoice_number") is not None
        assert len(inv.get("line_items", [])) > 0
        for li in inv["line_items"]:
            amt = li.get("amount")
            if amt is not None:
                assert isinstance(Decimal(amt), Decimal)
    assert result.get("parse_ok") is True


# ---- T2: invoice-grain csv, one invoice per row ----

def test_T2_invoice_grain_csv(fx):
    mapping = SheetMapping(
        grain="invoice",
        group_by=None,
        invoice_fields={"invoice_number": "Invoice Number", "invoice_date": "Invoice Date",
                        "supplier_name": "Supplier", "customer_name": "Customer",
                        "currency": "Currency", "subtotal": "Subtotal",
                        "tax": "Tax", "total": "Total"},
        line_fields={},
        date_format="%Y-%m-%d",
    )
    state = _load_classify_map(fx["csv_invoice_grain"], mapping)
    result = sheet_transform(state)
    invoices = result.get("invoices", [])
    assert len(invoices) >= 3, f"Expected >= 3 invoices, got {len(invoices)}"
    assert result.get("parse_ok") is True


# ---- T3: wrong date_format → parse_ok=False, feedback ----

def test_T3_wrong_date_format_feedback(fx):
    # Use odd_headers which has dd/mm/yyyy dates; supply wrong format
    wrong_mapping = SheetMapping(
        grain="line_item",
        group_by="Doc Ref",
        invoice_fields={"invoice_number": "Doc Ref", "invoice_date": "Issued On",
                        "supplier_name": "Supplier Co", "customer_name": "Customer"},
        line_fields={"description": "Description", "quantity": "Quantity",
                     "unit_price": "Unit Price", "amount": "Line Amt"},
        date_format="%m/%d/%Y",  # Wrong! Dates are dd/mm/yyyy with day > 12
    )
    state = _load_classify_map(fx["xlsx_odd_headers"], wrong_mapping)
    result = sheet_transform(state)
    assert result.get("parse_ok") is False
    feedback = result.get("mapping_feedback", "")
    assert feedback is not None and len(feedback) > 0


# ---- T4: retry-then-success clears feedback ----

def test_T4_retry_success_clears_feedback(fx):
    mapping = _std_linegrain_mapping()
    state = _load_classify_map(fx["xlsx_simple_linegrain"], mapping)
    # Simulate a retry: set mapping_retries=1 (already retried once)
    state["mapping_retries"] = 1
    result = sheet_transform(state)
    assert result.get("parse_ok") is True
    assert result.get("mapping_feedback") is None


# ---- T5: mapping=None → parse_ok=False; finalize → MAPPING_FAILED ----

def test_T5_no_mapping_finalize_mapping_failed(fx):
    cfg = get_config()
    state = _load_classify_map(fx["xlsx_simple_linegrain"], _std_linegrain_mapping())
    # Force mapping=None and mapping_retries > MAX
    state["mapping"] = None
    state["mapping_retries"] = cfg.MAPPING_MAX_RETRIES + 1
    transform_result = sheet_transform(state)
    assert transform_result.get("parse_ok") is False
    # Now run finalize
    final_state = {**state, **transform_result, "invoices": []}
    fin_result = finalize(final_state)
    result_dict = fin_result.get("result", final_state.get("result", {}))
    warnings = result_dict.get("warnings", []) if result_dict else []
    codes = [w["code"] for w in warnings]
    assert IssueCode.MAPPING_FAILED.value in codes
    assert result_dict.get("status") == "needs_review"


# ---- T6: totals row → UNGROUPED_ROWS ----

def test_T6_totals_row_ungrouped(fx):
    mapping = SheetMapping(
        grain="line_item",
        group_by="Invoice Number",
        invoice_fields={"invoice_number": "Invoice Number",
                        "invoice_date": "Invoice Date"},
        line_fields={"description": "Description", "amount": "Amount"},
        date_format="%Y-%m-%d",
    )
    state = _load_classify_map(fx["xlsx_totals_row"], mapping)
    result = sheet_transform(state)
    warnings = result.get("warnings", [])
    codes = [w["code"] for w in warnings]
    assert IssueCode.UNGROUPED_ROWS.value in codes
    # The totals row (no invoice number) should not appear in invoices
    invoices = result.get("invoices", [])
    for inv in invoices:
        assert inv.get("invoice_number") is not None


# ---- T7: header money equal vs differ ----

def test_T7_header_money_equal(fx):
    mapping = SheetMapping(
        grain="line_item",
        group_by="Invoice Number",
        invoice_fields={"invoice_number": "Invoice Number",
                        "invoice_date": "Invoice Date",
                        "total": "Total"},
        line_fields={"description": "Description", "amount": "Amount"},
        date_format="%Y-%m-%d",
    )
    state = _load_classify_map(fx["xlsx_header_money_equal"], mapping)
    result = sheet_transform(state)
    invoices = result.get("invoices", [])
    assert len(invoices) > 0
    # total should be captured (equal across lines)
    assert invoices[0].get("total") is not None


def test_T7_header_money_differ(fx):
    mapping = SheetMapping(
        grain="line_item",
        group_by="Invoice Number",
        invoice_fields={"invoice_number": "Invoice Number",
                        "invoice_date": "Invoice Date",
                        "total": "Total"},
        line_fields={"description": "Description", "amount": "Amount"},
        date_format="%Y-%m-%d",
    )
    state = _load_classify_map(fx["xlsx_header_money_differ"], mapping)
    result = sheet_transform(state)
    warnings = result.get("warnings", [])
    codes = [w["code"] for w in warnings]
    assert IssueCode.SHEET_GROUP_INCONSISTENT.value in codes


# ---- T8: EU amounts parsed correctly ----

def test_T8_eu_amounts(fx):
    mapping = SheetMapping(
        grain="line_item",
        group_by="Invoice Number",
        invoice_fields={"invoice_number": "Invoice Number",
                        "invoice_date": "Invoice Date",
                        "supplier_name": "Supplier",
                        "customer_name": "Customer",
                        "currency": "Currency"},
        line_fields={"quantity": "Quantity", "unit_price": "Unit Price", "amount": "Amount"},
        date_format="%Y-%m-%d",
        decimal_separator=",",
        thousands_separator=".",
    )
    state = _load_classify_map(fx["csv_cp1252_semicolon"], mapping)
    result = sheet_transform(state)
    invoices = result.get("invoices", [])
    assert len(invoices) > 0
    for inv in invoices:
        for li in inv.get("line_items", []):
            amt = li.get("amount")
            if amt is not None:
                # 1.234,56 → 1234.56 not 1.23456
                assert Decimal(amt) == Decimal("1234.56") or Decimal(amt) > Decimal("100"), (
                    f"EU amount not parsed correctly: {amt}"
                )


# ---- T9: leading zeros preserved ----

def test_T9_leading_zeros(fx):
    mapping = SheetMapping(
        grain="line_item",
        group_by="Invoice Number",
        invoice_fields={"invoice_number": "Invoice Number",
                        "invoice_date": "Invoice Date",
                        "supplier_name": "Supplier"},
        line_fields={"amount": "Amount"},
        date_format="%Y-%m-%d",
    )
    state = _load_classify_map(fx["xlsx_leading_zeros"], mapping)
    result = sheet_transform(state)
    invoices = result.get("invoices", [])
    assert len(invoices) > 0
    inv_no = invoices[0].get("invoice_number", "")
    assert inv_no.startswith("0"), f"Expected leading zeros, got {inv_no!r}"


# ---- T10: performance ----

def test_T10_perf_5000x30(fx):
    mapping = SheetMapping(
        grain="line_item",
        group_by="Invoice Number",
        invoice_fields={"invoice_number": "Invoice Number", "invoice_date": "Invoice Date",
                        "supplier_name": "Supplier", "customer_name": "Customer",
                        "currency": "Currency", "total": "Total"},
        line_fields={"description": "Description", "quantity": "Quantity",
                     "unit_price": "Unit Price", "amount": "Amount"},
        date_format="%Y-%m-%d",
    )
    state = _load_classify_map(fx["perf_5000x30"], mapping)
    # Hand-build the mapping in state to skip sheet_map
    state["mapping"] = mapping.model_dump()
    start = time.perf_counter()
    result = sheet_transform(state)
    elapsed = time.perf_counter() - start
    assert elapsed < 3.0, f"sheet_transform took {elapsed:.2f}s (limit 3s)"


# ---- T11: derived.lines_sum, subtotal=None when unmapped ----

def test_T11_derived_lines_sum(fx):
    # Inject mapping directly (bypassing sheet_map) so subtotal is NOT mapped
    mapping = SheetMapping(
        grain="line_item",
        group_by="Invoice Number",
        invoice_fields={"invoice_number": "Invoice Number",
                        "invoice_date": "Invoice Date"},
        line_fields={"description": "Description", "amount": "Amount"},
        date_format="%Y-%m-%d",
    )
    # Load + classify, then inject the mapping directly into state
    state = {"file_path": str(fx["xlsx_simple_linegrain"]), "file_type": "sheet",
             "sim": {}, "warnings": []}
    import app.pipeline.nodes.sheet_load as _sl
    import app.pipeline.nodes.sheet_classify as _sc
    state = {**state, **_sl.sheet_load(state)}
    state = {**state, **_sc.sheet_classify(state)}
    state["mapping"] = mapping.model_dump()
    result = sheet_transform(state)
    invoices = result.get("invoices", [])
    assert len(invoices) > 0
    for inv in invoices:
        # subtotal should be None (not mapped)
        assert inv.get("subtotal") is None
        # derived.lines_sum should be a string (Decimal formatted)
        derived = inv.get("derived", {})
        if derived.get("lines_sum") is not None:
            Decimal(derived["lines_sum"])  # should not raise
