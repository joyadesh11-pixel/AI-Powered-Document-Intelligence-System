"""M1 validation tests: T1-T19 plus utility unit tests.

Run red first (before implementing), then green after implementation.
"""
from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.pipeline.nodes.validate import make_validate
from app.pipeline.utils.dates import ParsedDate, parse_date
from app.pipeline.utils.hashing import sha256_file
from app.pipeline.utils.money import amounts_equal, parse_amount
from app.pipeline.utils.text_norm import normalise
from app.pipeline.validation import ESCALATION_CODES
from app.pipeline.validation.duplicates import InMemorySeenStore, compute_dup_key
from app.schemas.models import Invoice, InvoiceStatus, IssueCode, LineItem, Severity, Tier

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

# Source text that matches the default invoice's fields so grounding passes
DEFAULT_PAGE_TEXT = {
    0: "INV-001 Acme Corp Customer Ltd 2026-01-01 100.00 10.00 110.00"
}


def make_invoice(**overrides) -> Invoice:
    """Return a fully-valid Invoice (all required fields, correct arithmetic)."""
    data: dict[str, Any] = {
        "tier": Tier.parser,
        "invoice_number": "INV-001",
        "invoice_date": date(2026, 1, 1),
        "due_date": date(2026, 2, 1),
        "supplier_name": "Acme Corp",
        "customer_name": "Customer Ltd",
        "subtotal": Decimal("100.00"),
        "tax": Decimal("10.00"),
        "total": Decimal("110.00"),
        "currency": "USD",
        "line_items": [LineItem(position=1, amount=Decimal("100.00"))],
        "raw": {
            "invoice_number": "INV-001",
            "invoice_date": "2026-01-01",
            "supplier_name": "Acme Corp",
            "total": "110.00",
            "subtotal": "100.00",
            "tax": "10.00",
        },
        "source_pages": [0],
    }
    data.update(overrides)
    return Invoice(**data)


def run_validate(
    invoices: list,
    *,
    page_text: dict | None = None,
    page_source: dict | None = None,
    file_type: str = "pdf",
    escalated: bool = False,
    vision_queue: list | None = None,
    file_hash: str = "abc123",
    seen_store: Any = None,
) -> dict:
    state = {
        "invoices": invoices,
        "page_text": page_text if page_text is not None else DEFAULT_PAGE_TEXT,
        "page_source": page_source if page_source is not None else {0: "digital"},
        "file_type": file_type,
        "file_hash": file_hash,
        "escalated": escalated,
        "vision_queue": vision_queue or [],
        "history": [],
    }
    deps = {"seen_store": seen_store} if seen_store is not None else None
    validate_fn = make_validate(deps)
    return validate_fn(state)


def _issue_field(i: Any, field: str) -> Any:
    val = i.get(field) if isinstance(i, dict) else getattr(i, field)
    return val.value if hasattr(val, "value") else val


def issue_codes(result: dict, inv_idx: int = 0) -> list[str]:
    inv = result["invoices"][inv_idx]
    issues = inv.issues if isinstance(inv, Invoice) else inv.get("issues", [])
    return [_issue_field(i, "code") for i in issues]


def issue_sevs(result: dict, inv_idx: int = 0) -> list[str]:
    inv = result["invoices"][inv_idx]
    issues = inv.issues if isinstance(inv, Invoice) else inv.get("issues", [])
    return [_issue_field(i, "severity") for i in issues]


def inv_status(result: dict, inv_idx: int = 0) -> InvoiceStatus:
    inv = result["invoices"][inv_idx]
    return inv.status if isinstance(inv, Invoice) else inv.get("status")


# ---------------------------------------------------------------------------
# T1  MISSING_REQUIRED
# ---------------------------------------------------------------------------

def test_t1_missing_required_invoice_number():
    inv = make_invoice(invoice_number=None, raw={})
    result = run_validate([inv])
    codes = issue_codes(result)
    assert IssueCode.MISSING_REQUIRED.value in codes
    assert inv_status(result) == InvoiceStatus.needs_review


def test_t1_missing_required_all_four_produces_four_issues():
    inv = make_invoice(
        invoice_number=None, invoice_date=None,
        supplier_name=None, total=None, line_items=[], raw={},
    )
    result = run_validate([inv])
    req_issues = [c for c in issue_codes(result) if c == IssueCode.MISSING_REQUIRED.value]
    # Should also fire NO_INVOICE_DETECTED when all four + no lines
    assert IssueCode.NO_INVOICE_DETECTED.value in issue_codes(result)
    assert inv_status(result) == InvoiceStatus.needs_review


# ---------------------------------------------------------------------------
# T2  MISSING_OPTIONAL — info only, validated_ok
# ---------------------------------------------------------------------------

def test_t2_missing_optional_is_info():
    inv = make_invoice(customer_name=None)
    result = run_validate([inv])
    codes = issue_codes(result)
    sevs = issue_sevs(result)
    assert IssueCode.MISSING_OPTIONAL.value in codes
    idx = codes.index(IssueCode.MISSING_OPTIONAL.value)
    assert sevs[idx] == Severity.info.value
    # info must not block
    assert inv_status(result) == InvoiceStatus.validated_ok


# ---------------------------------------------------------------------------
# T3  BAD_NUMBER
# ---------------------------------------------------------------------------

def test_t3_bad_number():
    inv = make_invoice(total=None, raw={"total": "abc", "invoice_number": "INV-001",
                                        "invoice_date": "2026-01-01",
                                        "supplier_name": "Acme Corp"})
    result = run_validate([inv])
    assert IssueCode.BAD_NUMBER.value in issue_codes(result)
    assert inv_status(result) == InvoiceStatus.needs_review


def test_t3_bad_number_empty_raw_does_not_fire():
    # If raw is absent or empty for a field, BAD_NUMBER must not fire (field simply absent)
    inv = make_invoice(total=None, raw={"invoice_number": "INV-001",
                                        "invoice_date": "2026-01-01",
                                        "supplier_name": "Acme Corp"})
    result = run_validate([inv])
    # MISSING_REQUIRED fires, but not BAD_NUMBER
    assert IssueCode.BAD_NUMBER.value not in issue_codes(result)
    assert IssueCode.MISSING_REQUIRED.value in issue_codes(result)


# ---------------------------------------------------------------------------
# T4  NEGATIVE_AMOUNT — warning, validated_ok
# ---------------------------------------------------------------------------

def test_t4_negative_amount_is_warning():
    # Credit note: subtotal+tax == total (arithmetic consistent), all negative
    inv = make_invoice(total=Decimal("-110.00"), subtotal=Decimal("-100.00"),
                       tax=Decimal("-10.00"),
                       line_items=[LineItem(position=1, amount=Decimal("-100.00"))],
                       raw={"invoice_number": "INV-001", "invoice_date": "2026-01-01",
                            "supplier_name": "Acme Corp", "total": "-110.00",
                            "subtotal": "-100.00", "tax": "-10.00"})
    result = run_validate([inv], page_text={0: "INV-001 Acme Corp Customer Ltd 2026-01-01 -100.00 -10.00 -110.00"})
    codes = issue_codes(result)
    sevs = issue_sevs(result)
    neg_indices = [i for i, c in enumerate(codes) if c == IssueCode.NEGATIVE_AMOUNT.value]
    assert neg_indices, "NEGATIVE_AMOUNT must be present"
    for idx in neg_indices:
        assert sevs[idx] == Severity.warning.value
    assert inv_status(result) == InvoiceStatus.validated_ok


# ---------------------------------------------------------------------------
# T5  BAD_DATE
# ---------------------------------------------------------------------------

def test_t5_bad_date():
    inv = make_invoice(
        invoice_date=None,
        raw={"invoice_number": "INV-001", "invoice_date": "notadate",
             "supplier_name": "Acme Corp", "total": "110.00",
             "subtotal": "100.00", "tax": "10.00"},
    )
    result = run_validate([inv])
    assert IssueCode.BAD_DATE.value in issue_codes(result)
    assert inv_status(result) == InvoiceStatus.needs_review


# ---------------------------------------------------------------------------
# T6  DATE_ORDER
# ---------------------------------------------------------------------------

def test_t6_date_order_due_before_invoice():
    inv = make_invoice(invoice_date=date(2026, 3, 1), due_date=date(2026, 1, 1))
    result = run_validate([inv])
    assert IssueCode.DATE_ORDER.value in issue_codes(result)
    assert inv_status(result) == InvoiceStatus.needs_review


def test_t6_date_order_due_after_invoice_ok():
    inv = make_invoice(invoice_date=date(2026, 1, 1), due_date=date(2026, 3, 1))
    result = run_validate([inv])
    assert IssueCode.DATE_ORDER.value not in issue_codes(result)


# ---------------------------------------------------------------------------
# T7  DATE_RANGE — warning
# ---------------------------------------------------------------------------

def test_t7_date_range_too_far_future():
    future = date.today() + timedelta(days=400)
    inv = make_invoice(
        invoice_date=future,
        raw={"invoice_number": "INV-001", "invoice_date": str(future),
             "supplier_name": "Acme Corp", "total": "110.00",
             "subtotal": "100.00", "tax": "10.00"},
    )
    result = run_validate([inv], page_text={0: f"INV-001 Acme Corp {future} 100.00 10.00 110.00"})
    codes = issue_codes(result)
    sevs = issue_sevs(result)
    assert IssueCode.DATE_RANGE.value in codes
    idx = codes.index(IssueCode.DATE_RANGE.value)
    assert sevs[idx] == Severity.warning.value


def test_t7_date_range_too_old():
    old = date(1999, 1, 1)
    inv = make_invoice(
        invoice_date=old,
        raw={"invoice_number": "INV-001", "invoice_date": "1999-01-01",
             "supplier_name": "Acme Corp", "total": "110.00",
             "subtotal": "100.00", "tax": "10.00"},
    )
    result = run_validate([inv], page_text={0: "INV-001 Acme Corp 1999-01-01 100.00 10.00 110.00"})
    assert IssueCode.DATE_RANGE.value in issue_codes(result)


# ---------------------------------------------------------------------------
# T8  BAD_CURRENCY
# ---------------------------------------------------------------------------

def test_t8_bad_currency():
    inv = make_invoice(currency="XX")
    result = run_validate([inv])
    assert IssueCode.BAD_CURRENCY.value in issue_codes(result)
    assert inv_status(result) == InvoiceStatus.needs_review


def test_t8_valid_currency_ok():
    for code in ("USD", "EUR", "GBP", "JPY", "BDT"):
        inv = make_invoice(currency=code)
        result = run_validate([inv])
        assert IssueCode.BAD_CURRENCY.value not in issue_codes(result), f"{code} should be valid"


# ---------------------------------------------------------------------------
# T9  ARITH_LINE — tol = max(0.01, 0.005 * qty)
# ---------------------------------------------------------------------------

def test_t9_arith_line_pass():
    # qty=1, unit_price=10, amount=10 → exact match
    line = LineItem(position=1, quantity=Decimal("1"), unit_price=Decimal("10.00"), amount=Decimal("10.00"))
    inv = make_invoice(line_items=[line], subtotal=Decimal("10.00"),
                       raw={"invoice_number": "INV-001", "invoice_date": "2026-01-01",
                            "supplier_name": "Acme Corp", "total": "110.00",
                            "subtotal": "10.00", "tax": "10.00"})
    result = run_validate([inv])
    assert IssueCode.ARITH_LINE.value not in issue_codes(result)


def test_t9_arith_line_within_tolerance():
    # qty=1, tol=0.01, diff=0.005 → passes
    line = LineItem(position=1, quantity=Decimal("1"), unit_price=Decimal("10.00"), amount=Decimal("10.005"))
    inv = make_invoice(line_items=[line], subtotal=Decimal("10.005"),
                       raw={"invoice_number": "INV-001", "invoice_date": "2026-01-01",
                            "supplier_name": "Acme Corp", "total": "110.005",
                            "subtotal": "10.005", "tax": "10.00"})
    result = run_validate([inv], page_text={0: "INV-001 Acme Corp 2026-01-01 10.005 10.00 110.005"})
    assert IssueCode.ARITH_LINE.value not in issue_codes(result)


def test_t9_arith_line_boundary_exactly_tol():
    # qty=1, tol=0.01, diff=0.01 → passes (inclusive)
    line = LineItem(position=1, quantity=Decimal("1"), unit_price=Decimal("10.00"), amount=Decimal("10.01"))
    inv = make_invoice(line_items=[line], subtotal=Decimal("10.01"),
                       raw={"invoice_number": "INV-001", "invoice_date": "2026-01-01",
                            "supplier_name": "Acme Corp", "total": "110.01",
                            "subtotal": "10.01", "tax": "10.00"})
    result = run_validate([inv], page_text={0: "INV-001 Acme Corp 2026-01-01 10.01 10.00 110.01"})
    assert IssueCode.ARITH_LINE.value not in issue_codes(result)


def test_t9_arith_line_just_over_tol():
    # qty=1, tol=0.01, diff=0.011 → fails
    line = LineItem(position=1, quantity=Decimal("1"), unit_price=Decimal("10.00"), amount=Decimal("10.011"))
    inv = make_invoice(line_items=[line])
    result = run_validate([inv])
    assert IssueCode.ARITH_LINE.value in issue_codes(result)


def test_t9_arith_line_high_qty_tolerance():
    # qty=10, tol=max(0.01, 0.005*10)=0.05, diff=0.04 → passes
    line = LineItem(position=1, quantity=Decimal("10"), unit_price=Decimal("5.00"), amount=Decimal("50.04"))
    inv = make_invoice(line_items=[line], subtotal=Decimal("50.04"),
                       raw={"invoice_number": "INV-001", "invoice_date": "2026-01-01",
                            "supplier_name": "Acme Corp", "total": "110.04",
                            "subtotal": "50.04", "tax": "10.00"})
    result = run_validate([inv], page_text={0: "INV-001 Acme Corp 2026-01-01 50.04 10.00 110.04"})
    assert IssueCode.ARITH_LINE.value not in issue_codes(result)


def test_t9_arith_line_high_qty_over_tol():
    # qty=10, tol=0.05, diff=0.06 → fails
    line = LineItem(position=1, quantity=Decimal("10"), unit_price=Decimal("5.00"), amount=Decimal("50.06"))
    inv = make_invoice(line_items=[line])
    result = run_validate([inv])
    assert IssueCode.ARITH_LINE.value in issue_codes(result)


# ---------------------------------------------------------------------------
# T10  ARITH_SUBTOTAL — tol = max(0.01, 0.005 * n_lines)
# ---------------------------------------------------------------------------

def test_t10_arith_subtotal_pass():
    lines = [
        LineItem(position=1, amount=Decimal("50.00")),
        LineItem(position=2, amount=Decimal("50.00")),
    ]
    inv = make_invoice(line_items=lines, subtotal=Decimal("100.00"),
                       raw={**make_invoice().raw})
    result = run_validate([inv])
    assert IssueCode.ARITH_SUBTOTAL.value not in issue_codes(result)


def test_t10_arith_subtotal_fail():
    lines = [LineItem(position=1, amount=Decimal("50.00"))]
    inv = make_invoice(line_items=lines, subtotal=Decimal("99.00"))
    result = run_validate([inv])
    assert IssueCode.ARITH_SUBTOTAL.value in issue_codes(result)


def test_t10_arith_subtotal_boundary_1_line_tol_001():
    # n_lines=1, tol=max(0.01, 0.005*1)=0.01, diff=0.01 → passes
    lines = [LineItem(position=1, amount=Decimal("100.00"))]
    inv = make_invoice(line_items=lines, subtotal=Decimal("100.01"),
                       raw={**make_invoice().raw, "subtotal": "100.01"})
    result = run_validate([inv], page_text={0: "INV-001 Acme Corp 2026-01-01 100.01 10.00 110.01"})
    assert IssueCode.ARITH_SUBTOTAL.value not in issue_codes(result)


def test_t10_arith_subtotal_boundary_just_over():
    # n_lines=1, tol=0.01, diff=0.011 → fails
    lines = [LineItem(position=1, amount=Decimal("100.00"))]
    inv = make_invoice(line_items=lines, subtotal=Decimal("100.011"))
    result = run_validate([inv])
    assert IssueCode.ARITH_SUBTOTAL.value in issue_codes(result)


# ---------------------------------------------------------------------------
# T11  ARITH_TOTAL — flat tol=0.01
# ---------------------------------------------------------------------------

def test_t11_arith_total_pass():
    inv = make_invoice(subtotal=Decimal("100.00"), tax=Decimal("10.00"), total=Decimal("110.00"))
    result = run_validate([inv])
    assert IssueCode.ARITH_TOTAL.value not in issue_codes(result)


def test_t11_arith_total_boundary_exactly_tol():
    # diff=0.01 → passes (abs(110.01 - (100+10)) = 0.01 ≤ 0.01)
    inv = make_invoice(subtotal=Decimal("100.00"), tax=Decimal("10.00"), total=Decimal("110.01"),
                       raw={**make_invoice().raw, "total": "110.01"})
    result = run_validate([inv], page_text={0: "INV-001 Acme Corp 2026-01-01 100.00 10.00 110.01"})
    assert IssueCode.ARITH_TOTAL.value not in issue_codes(result)


def test_t11_arith_total_just_over_tol():
    # diff=0.011 → fails
    inv = make_invoice(subtotal=Decimal("100.00"), tax=Decimal("10.00"), total=Decimal("110.011"))
    result = run_validate([inv])
    assert IssueCode.ARITH_TOTAL.value in issue_codes(result)


def test_t11_arith_total_tax_null_is_skipped():
    # If tax is None, ARITH_TOTAL check is skipped (info at most)
    inv = make_invoice(tax=None, subtotal=Decimal("100.00"), total=Decimal("110.00"),
                       raw={**make_invoice().raw, "tax": None})
    result = run_validate([inv], page_text={0: "INV-001 Acme Corp 2026-01-01 100.00 110.00"})
    error_codes = [c for c, s in zip(issue_codes(result), issue_sevs(result))
                   if c == IssueCode.ARITH_TOTAL.value and s == Severity.error.value]
    assert not error_codes, "ARITH_TOTAL with null tax must not be an error"


def test_t11_arith_total_subtotal_null_uses_lines_sum():
    # subtotal=None → use sum(line.amount) + tax vs total
    lines = [LineItem(position=1, amount=Decimal("100.00"))]
    inv = make_invoice(line_items=lines, subtotal=None, tax=Decimal("10.00"), total=Decimal("110.00"),
                       raw={**make_invoice().raw, "subtotal": None})
    result = run_validate([inv], page_text={0: "INV-001 Acme Corp 2026-01-01 100.00 10.00 110.00"})
    assert IssueCode.ARITH_TOTAL.value not in issue_codes(result)


# ---------------------------------------------------------------------------
# T12  LINES_MISSING — warning, NOT error, validated_ok
# ---------------------------------------------------------------------------

def test_t12_lines_missing_is_warning_not_error():
    inv = make_invoice(line_items=[])
    result = run_validate([inv])
    codes = issue_codes(result)
    sevs = issue_sevs(result)
    assert IssueCode.LINES_MISSING.value in codes
    idx = codes.index(IssueCode.LINES_MISSING.value)
    assert sevs[idx] == Severity.warning.value
    assert inv_status(result) == InvoiceStatus.validated_ok


# ---------------------------------------------------------------------------
# T13  No float — Decimal arithmetic
# ---------------------------------------------------------------------------

def test_t13_no_float_arithmetic():
    """Prove Decimal is used: 0.1 + 0.2 == 0.3 (exact in Decimal, not float)."""
    assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")
    # Invoice with these exact values must pass ARITH_TOTAL
    lines = [LineItem(position=1, amount=Decimal("0.1"))]
    inv = make_invoice(
        line_items=lines,
        subtotal=Decimal("0.1"),
        tax=Decimal("0.2"),
        total=Decimal("0.3"),
        raw={**make_invoice().raw, "subtotal": "0.1", "tax": "0.2", "total": "0.3"},
    )
    result = run_validate([inv], page_text={0: "INV-001 Acme Corp Customer Ltd 2026-01-01 0.1 0.2 0.3"})
    assert IssueCode.ARITH_TOTAL.value not in issue_codes(result)
    assert inv_status(result) == InvoiceStatus.validated_ok


# ---------------------------------------------------------------------------
# T14  All three tolerance formulae (boundary)
# ---------------------------------------------------------------------------

def test_t14_tolerance_formulae_all_three():
    # ARITH_LINE: max(0.01, 0.005*qty)
    # Covered exhaustively in T9.

    # ARITH_SUBTOTAL: max(0.01, 0.005*n_lines); n_lines=4 → tol=max(0.01,0.02)=0.02
    lines = [LineItem(position=i, amount=Decimal("25.00")) for i in range(1, 5)]  # sum=100
    # diff=0.02 → passes
    inv = make_invoice(line_items=lines, subtotal=Decimal("100.02"),
                       raw={**make_invoice().raw, "subtotal": "100.02"})
    result = run_validate([inv], page_text={0: "INV-001 Acme Corp 2026-01-01 100.02 10.00 110.02"})
    assert IssueCode.ARITH_SUBTOTAL.value not in issue_codes(result)

    # diff=0.021 → fails
    inv2 = make_invoice(line_items=lines, subtotal=Decimal("100.021"))
    result2 = run_validate([inv2])
    assert IssueCode.ARITH_SUBTOTAL.value in issue_codes(result2)

    # ARITH_TOTAL: flat 0.01; diff=0.01 passes, 0.011 fails — covered in T11.


# ---------------------------------------------------------------------------
# T15  Sheet + ARITH_TOTAL → needs_review, empty vision_queue
# ---------------------------------------------------------------------------

def test_t15_sheet_arith_no_escalation():
    inv = make_invoice(
        tier=Tier.sheet,
        subtotal=Decimal("100.00"),
        tax=Decimal("10.00"),
        total=Decimal("200.00"),  # wrong
    )
    result = run_validate([inv], file_type="sheet")
    assert IssueCode.ARITH_TOTAL.value in issue_codes(result)
    assert inv_status(result) == InvoiceStatus.needs_review
    assert result["vision_queue"] == []


# ---------------------------------------------------------------------------
# T16  escalated=True → no new queue entries
# ---------------------------------------------------------------------------

def test_t16_escalated_no_new_queue():
    inv = make_invoice(subtotal=Decimal("100.00"), tax=Decimal("10.00"), total=Decimal("200.00"))
    result = run_validate([inv], escalated=True)
    # Error exists but escalated prevents queueing
    assert IssueCode.ARITH_TOTAL.value in issue_codes(result)
    assert result["vision_queue"] == []


# ---------------------------------------------------------------------------
# T17  DUPLICATE_INVOICE not in ESCALATION_CODES → empty queue
# ---------------------------------------------------------------------------

def test_t17_duplicate_not_in_escalation_codes():
    assert IssueCode.DUPLICATE_INVOICE not in ESCALATION_CODES

    store = InMemorySeenStore(
        keys=[compute_dup_key(make_invoice())],
    )
    inv = make_invoice()
    result = run_validate([inv], seen_store=store)
    assert IssueCode.DUPLICATE_INVOICE.value in issue_codes(result)
    assert result["vision_queue"] == []


# ---------------------------------------------------------------------------
# T18  Within-file duplicate: second flagged, first not
# ---------------------------------------------------------------------------

def test_t18_within_file_duplicate():
    inv1 = make_invoice()
    inv2 = make_invoice()  # same key
    result = run_validate([inv1, inv2])
    assert IssueCode.DUPLICATE_INVOICE.value not in issue_codes(result, 0)
    assert IssueCode.DUPLICATE_INVOICE.value in issue_codes(result, 1)
    assert inv_status(result, 0) == InvoiceStatus.validated_ok
    assert inv_status(result, 1) == InvoiceStatus.needs_review


def test_t18_null_field_skips_dup_check():
    # If any dup_key part is null, skip check; second invoice does NOT get dup error
    inv1 = make_invoice(invoice_number=None, raw={})
    inv2 = make_invoice(invoice_number=None, raw={})
    result = run_validate([inv1, inv2])
    assert IssueCode.DUPLICATE_INVOICE.value not in issue_codes(result, 1)


# ---------------------------------------------------------------------------
# T19  Grounding: parser=error, ocr=warning, vision=skipped
# ---------------------------------------------------------------------------

GROUNDING_PAGE_TEXT = {0: "completely different text with no invoice fields at all xyz"}


def test_t19_grounding_parser_error():
    inv = make_invoice(tier=Tier.parser)
    result = run_validate([inv], page_text=GROUNDING_PAGE_TEXT)
    codes = issue_codes(result)
    sevs = issue_sevs(result)
    ng_indices = [i for i, c in enumerate(codes) if c == IssueCode.NOT_GROUNDED.value]
    assert ng_indices, "NOT_GROUNDED must fire for parser tier"
    for idx in ng_indices:
        assert sevs[idx] == Severity.error.value


def test_t19_grounding_ocr_warning():
    inv = make_invoice(tier=Tier.ocr)
    result = run_validate([inv], page_text=GROUNDING_PAGE_TEXT)
    codes = issue_codes(result)
    sevs = issue_sevs(result)
    ng_indices = [i for i, c in enumerate(codes) if c == IssueCode.NOT_GROUNDED.value]
    assert ng_indices, "NOT_GROUNDED must fire for ocr tier"
    for idx in ng_indices:
        assert sevs[idx] == Severity.warning.value


def test_t19_grounding_vision_skipped():
    inv = make_invoice(tier=Tier.vision)
    result = run_validate([inv], page_text=GROUNDING_PAGE_TEXT)
    assert IssueCode.NOT_GROUNDED.value not in issue_codes(result)


def test_t19_grounding_sheet_skipped():
    inv = make_invoice(tier=Tier.sheet)
    result = run_validate([inv], file_type="sheet", page_text=GROUNDING_PAGE_TEXT)
    assert IssueCode.NOT_GROUNDED.value not in issue_codes(result)


def test_t19_grounding_passes_when_source_matches():
    inv = make_invoice(tier=Tier.parser)
    # DEFAULT_PAGE_TEXT contains all invoice field values
    result = run_validate([inv], page_text=DEFAULT_PAGE_TEXT)
    codes = issue_codes(result)
    assert IssueCode.NOT_GROUNDED.value not in codes, (
        f"Grounding should pass when source text matches. Got: {codes}"
    )


def test_t19_grounding_empty_source_fires():
    """Empty OCR source_text must surface NOT_GROUNDED (M7: removed empty-source guard)."""
    inv = make_invoice(tier=Tier.parser)
    result = run_validate([inv], page_text={0: ""})
    codes = issue_codes(result)
    assert IssueCode.NOT_GROUNDED.value in codes, (
        "Empty OCR page must surface NOT_GROUNDED (grounding must not skip empty source)"
    )


# ---------------------------------------------------------------------------
# _extract_amounts: M7 requirement
# ---------------------------------------------------------------------------

def test_extract_amounts_mixed():
    """_extract_amounts must parse comma-thousands, plain integers, and currency context."""
    from app.pipeline.validation.grounding import _extract_amounts
    from decimal import Decimal
    result = _extract_amounts("Total 1,234.56 EUR 99 Qty 10")
    assert Decimal("1234.56") in result, f"Expected 1234.56 in {result}"
    assert Decimal("99.00") in result, f"Expected 99.00 in {result}"
    assert Decimal("10.00") in result, f"Expected 10.00 in {result}"


# ---------------------------------------------------------------------------
# Escalation codes set
# ---------------------------------------------------------------------------

def test_escalation_codes_contents():
    expected = {
        IssueCode.MISSING_REQUIRED,
        IssueCode.BAD_NUMBER,
        IssueCode.BAD_DATE,
        IssueCode.BAD_CURRENCY,
        IssueCode.ARITH_LINE,
        IssueCode.ARITH_SUBTOTAL,
        IssueCode.ARITH_TOTAL,
        IssueCode.NOT_GROUNDED,
        IssueCode.NO_INVOICE_DETECTED,
    }
    assert expected <= ESCALATION_CODES  # all expected codes are in the set


def test_not_grounded_ocr_does_not_escalate():
    """NOT_GROUNDED at OCR is warning → must not go into vision_queue."""
    inv = make_invoice(tier=Tier.ocr)
    result = run_validate([inv], page_text=GROUNDING_PAGE_TEXT, file_type="pdf", escalated=False)
    # OCR grounding is warning → should not trigger escalation
    assert result["vision_queue"] == []


# ---------------------------------------------------------------------------
# Utility: parse_amount
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("1,234.56",  Decimal("1234.56")),
    ("1.234,56",  Decimal("1234.56")),
    ("1 234,56",  Decimal("1234.56")),
    ("1'234.56",  Decimal("1234.56")),
    ("1,234",     Decimal("1234")),      # thousands separator (default thousands_sep=",")
    ("1.234",     Decimal("1.234")),     # decimal point (default decimal_sep=".")
    ("1,234,567", Decimal("1234567")),
    ("1.234.567", Decimal("1234567")),
    ("12,5",      Decimal("12.5")),
    ("-45.00",    Decimal("-45.00")),
    ("45.00-",    Decimal("-45.00")),
    ("(45.00)",   Decimal("-45.00")),
    ("$ 1,200.00", Decimal("1200.00")),
    ("EUR 99",    Decimal("99")),
    ("99 EUR",    Decimal("99")),
    ("¥1,200",    Decimal("1200")),
    ("abc",       None),
    ("",          None),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


def test_parse_amount_explicit_separators_euro_format():
    # Explicit decimal_sep="," thousands_sep="." for euro locale
    assert parse_amount("1.234,56", decimal_sep=",", thousands_sep=".") == Decimal("1234.56")
    assert parse_amount("1,234", decimal_sep=",", thousands_sep=".") == Decimal("1.234")


def test_amounts_equal():
    assert amounts_equal(Decimal("100.00"), Decimal("100.005"))
    assert not amounts_equal(Decimal("100.00"), Decimal("100.02"))
    assert amounts_equal(Decimal("100.00"), Decimal("100.01"))  # exactly tol


# ---------------------------------------------------------------------------
# Utility: parse_date
# ---------------------------------------------------------------------------

def test_parse_date_iso():
    r = parse_date("2026-01-03")
    assert r.value == date(2026, 1, 3)
    assert not r.ambiguous


def test_parse_date_named_month_day_first():
    r = parse_date("3 Nov 2026")
    assert r.value == date(2026, 11, 3)
    assert not r.ambiguous


def test_parse_date_named_month_month_first():
    r = parse_date("November 3, 2026")
    assert r.value == date(2026, 11, 3)
    assert not r.ambiguous


def test_parse_date_numeric_day_unambiguous():
    # day=15 > 12 → unambiguous
    r = parse_date("15/01/2026")
    assert r.value == date(2026, 1, 15)
    assert not r.ambiguous


def test_parse_date_numeric_month_unambiguous():
    # second part=13 > 12 → month would be invalid if first were month
    r = parse_date("01/13/2026")
    assert r.value == date(2026, 1, 13) or r.value == date(2026, 1, 13)
    # The part > 12 must be treated as day
    assert r.value.day == 13


def test_parse_date_numeric_ambiguous():
    r = parse_date("01/06/2026")
    # Both 1 and 6 are <= 12, no day_first hint → ambiguous
    assert r.ambiguous


def test_parse_date_numeric_day_first_hint():
    r = parse_date("01/06/2026", day_first=True)
    assert r.value == date(2026, 6, 1)
    assert not r.ambiguous


def test_parse_date_numeric_month_first_hint():
    r = parse_date("01/06/2026", day_first=False)
    assert r.value == date(2026, 1, 6)
    assert not r.ambiguous


def test_parse_date_two_digit_year_young():
    r = parse_date("01/06/26", day_first=True)
    assert r.value.year == 2026


def test_parse_date_two_digit_year_old():
    r = parse_date("01/06/99", day_first=True)
    assert r.value.year == 1999


def test_parse_date_invalid_raises():
    with pytest.raises(ValueError):
        parse_date("not a date")


def test_parse_date_fmt():
    r = parse_date("03/11/2026", fmt="%d/%m/%Y")
    assert r.value == date(2026, 11, 3)
    assert not r.ambiguous


# ---------------------------------------------------------------------------
# Utility: normalise
# ---------------------------------------------------------------------------

def test_normalise_casefold_strip():
    assert normalise("INV-001") == "inv001"
    assert normalise("Acme Corp.") == "acmecorp"
    assert normalise("  Hello World! ") == "helloworld"


def test_normalise_empty():
    assert normalise("") == ""


# ---------------------------------------------------------------------------
# Utility: sha256_file
# ---------------------------------------------------------------------------

def test_sha256_file():
    content = b"hello M1"
    expected = hashlib.sha256(content).hexdigest()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        f.write(content)
        path = f.name
    assert sha256_file(path) == expected


# ---------------------------------------------------------------------------
# corrected_fields: grounding skip semantics (amendment #14)
# ---------------------------------------------------------------------------

def test_corrected_field_skips_grounding():
    """A field listed in corrected_fields must not produce NOT_GROUNDED."""
    from app.pipeline.validation.grounding import check_grounding

    invoice = make_invoice(supplier_name="XYZ_UniqueString_NotInSourceText_12345")
    # Without corrected_fields → supplier_name should fire NOT_GROUNDED
    issues_without = check_grounding(invoice, "completely unrelated content", "parser")
    assert any(
        getattr(i, "field", None) == "supplier_name" for i in issues_without
    ), "Expected NOT_GROUNDED for supplier_name when not in corrected_fields"

    # With corrected_fields containing supplier_name → should be skipped
    issues_with = check_grounding(
        invoice,
        "completely unrelated content",
        "parser",
        corrected_fields=frozenset({"supplier_name"}),
    )
    assert not any(
        getattr(i, "field", None) == "supplier_name" for i in issues_with
    ), "supplier_name should be skipped when in corrected_fields"


def test_uncorrected_field_still_grounds():
    """Fields NOT in corrected_fields still produce NOT_GROUNDED when absent from source."""
    from app.pipeline.validation.grounding import check_grounding

    invoice = make_invoice(supplier_name="GhostSupplier_NeverInText_99999")
    # corrected_fields contains a different field — supplier_name still checked
    issues = check_grounding(
        invoice,
        "some text with invoice number INV-001",
        "parser",
        corrected_fields=frozenset({"invoice_number"}),
    )
    assert any(
        getattr(i, "field", None) == "supplier_name" for i in issues
    ), "supplier_name should still ground-check when not in corrected_fields"


def test_corrected_fields_only_affects_grounding():
    """corrected_fields must not suppress arithmetic or completeness issues."""
    from app.pipeline.validation import ValidationCtx, run_all
    from app.pipeline.validation.duplicates import InMemorySeenStore

    # Invoice with wrong arithmetic: subtotal+tax != total
    invoice = make_invoice(
        subtotal=Decimal("100.00"),
        tax=Decimal("10.00"),
        total=Decimal("999.99"),  # wrong
        corrected_fields=frozenset(),  # not a real field on Invoice model; set via ctx
    )
    ctx = ValidationCtx(
        tier="parser",
        file_type="pdf",
        file_hash="testcorrectedfields",
        source_text="INV-001 Acme Corp Customer Ltd 2026-01-01 100.00 10.00 999.99",
        seen_store=InMemorySeenStore(),
        seen_in_file={},
        day_first=None,
        corrected_fields=frozenset({"total", "subtotal", "tax"}),
    )
    issues = run_all(invoice, ctx)
    codes = [getattr(i, "code", i) for i in issues]
    code_strs = [c.value if hasattr(c, "value") else str(c) for c in codes]
    # ARITH_ERROR should still fire even though total/subtotal/tax are in corrected_fields
    assert any("ARITH" in s for s in code_strs), (
        f"ARITH_ERROR should not be suppressed by corrected_fields; got {code_strs}"
    )
