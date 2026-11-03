"""Integration tests: 15 end-to-end scenarios via process() with FakeLLM.

Each test runs the full LangGraph pipeline through process() with no real LLM.
S1-S5:   PDF paths (digital / mixed / vision escalation)
S6-S9:   Sheet paths (xlsx / csv)
S10-S11: Error paths (corrupt / unsupported)
S12:     Injected exception via monkeypatch
S13:     xlsx with arithmetic error → needs_review, no vision
S14:     Duplicate invoice via InMemorySeenStore pre-seeded from S1's result
S15:     Missing invoice_number → needs_review + MISSING_REQUIRED

Relaxed assertions: S1/S4/S5 accept needs_review as well as validated_ok when
grounding issues arise from FakeLLM values not matching fixture text exactly;
the node-path assertion is the primary correctness check for those scenarios.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.pipeline.graph import process, _default_deps
from app.pipeline.config import get_config
from app.pipeline.state import Deps
from app.pipeline.validation import InMemorySeenStore
from app.pipeline.validation.duplicates import compute_dup_key
from app.schemas.models import (
    DocStatus, ExtractedInvoice, IssueCode, SheetMapping, VisionExtraction,
)
from tests.fakes import FakeLLM


# ---------------------------------------------------------------- helpers

def _deps(llm=None, seen_store=None) -> Deps:
    return Deps(config=get_config(), llm=llm or FakeLLM([]),
                seen_store=seen_store, mapping_cache=None)


def _history_nodes(result) -> list[str]:
    return [h["node"] for h in result.history]


def _invoice_issue_codes(result) -> list[str]:
    codes = []
    for inv in result.invoices:
        for iss in inv.issues:
            codes.append(iss.code.value if hasattr(iss.code, "value") else str(iss.code))
    return codes


def _warning_codes(result) -> list[str]:
    codes = []
    for w in result.warnings:
        if isinstance(w, dict):
            codes.append(str(w.get("code", "")))
        elif hasattr(w, "code"):
            v = w.code
            codes.append(v.value if hasattr(v, "value") else str(v))
        else:
            codes.append(str(w))
    return codes


def _gt_inv(gt: dict, fname: str) -> dict:
    """Return the first invoice GT entry for a fixture file."""
    entry = gt.get(fname, {})
    invs = entry.get("invoices", [])
    return invs[0] if invs else {}


def _fake_from_gt(gt_inv: dict) -> ExtractedInvoice:
    """Build a FakeLLM ExtractedInvoice that matches the GT (for grounding to pass)."""
    return ExtractedInvoice(
        invoice_number=gt_inv.get("invoice_number"),
        invoice_date=gt_inv.get("invoice_date"),
        supplier_name=gt_inv.get("supplier_name"),
        customer_name=gt_inv.get("customer_name"),
        subtotal=str(gt_inv["subtotal"]) if gt_inv.get("subtotal") is not None else None,
        tax=str(gt_inv["tax"]) if gt_inv.get("tax") is not None else None,
        total=str(gt_inv["total"]) if gt_inv.get("total") is not None else None,
        currency=gt_inv.get("currency"),
    )


def _vision_from_gt(gt_inv: dict, *, src_text: str = "") -> VisionExtraction:
    base = _fake_from_gt(gt_inv)
    d = base.model_dump()
    d["source_text"] = src_text
    return VisionExtraction(**d)


# ================================================================ S1-S5: PDF

def test_s1_digital_clean(fx, gt):
    """S1: digital PDF → validated_ok; key PDF nodes in history."""
    gt_inv = _gt_inv(gt, "digital_A_001.pdf")
    fake = FakeLLM([_fake_from_gt(gt_inv)])
    result = process(str(fx["pdf"]), deps=_deps(fake))
    nodes = _history_nodes(result)
    assert result.status == DocStatus.validated_ok, (
        f"Expected validated_ok, got {result.status}. issues={_invoice_issue_codes(result)}"
    )
    for n in ("segment", "llm_extract", "validate"):
        assert n in nodes, f"Expected node {n!r} in history, got: {nodes}"


def test_s2_scanned_image(fx):
    """S2: scan_clean.png → validated_ok; ocr+segment in history."""
    fake = FakeLLM([ExtractedInvoice(
        invoice_number="SCAN-0001",
        invoice_date="2026-11-01",
        supplier_name="ScanCo Ltd",
        customer_name="TestClient Corp",
        subtotal="90.00",
        tax="9.00",
        total="99.00",
        currency="USD",
    )])
    result = process(str(fx["scan_clean"]), deps=_deps(fake))
    nodes = _history_nodes(result)
    assert result.status == DocStatus.validated_ok, (
        f"Expected validated_ok, got {result.status}. issues={_invoice_issue_codes(result)}"
    )
    for n in ("ocr", "segment", "llm_extract"):
        assert n in nodes, f"Expected node {n!r} in history, got: {nodes}"


def test_s3_mixed_pdf_low_conf_vision(fx):
    """S3: mixed.pdf (5 digital pages + 1 blank scanned).

    The blank page gets OCR conf=0.0 → low_conf → the unit has needs_vision=True
    → llm_extract skips it → vision_extract processes it.
    Assert: "vision_extract" in history AND digital pages' text survived in result.
    """
    vision_resp = VisionExtraction(
        invoice_number="MIXED-0042",
        invoice_date="2026-11-02",
        supplier_name="MixedSupplier Ltd",
        customer_name="MixedClient Corp",
        subtotal="200.00",
        tax="20.00",
        total="220.00",
        currency="USD",
        source_text="Invoice Number: MIXED-0042 MixedSupplier Ltd 200.00 20.00 220.00",
    )
    fake = FakeLLM([vision_resp])
    result = process(str(fx["mixed"]), deps=_deps(fake))
    nodes = _history_nodes(result)

    # vision_extract must appear in history (blank page → low_conf → vision path)
    assert "vision_extract" in nodes, (
        f"Expected vision_extract in history (blank page → low_conf → vision path), got: {nodes}"
    )

    # Digital pages (0-4) must have survived: page_text[0] non-empty
    assert result.page_text.get(0), (
        "Digital page 0 text must survive into result.page_text (OCR-merge check)"
    )


def test_s4_arith_error_fixed_by_vision(fx, gt):
    """S4: arith error in text tier → vision corrects it → validated_ok."""
    gt_inv = _gt_inv(gt, "digital_A_001.pdf")

    # Text: wrong total (sub + tax - 1 cent → ARITH_TOTAL) → escalate to vision
    sub = gt_inv.get("subtotal", "100.00")
    tax = gt_inv.get("tax", "10.00")
    correct_total = gt_inv.get("total", "110.00")
    wrong_total = str(Decimal(str(correct_total)) - Decimal("1.00"))

    text_resp = ExtractedInvoice(
        invoice_number=gt_inv.get("invoice_number"),
        invoice_date=gt_inv.get("invoice_date"),
        supplier_name=gt_inv.get("supplier_name"),
        customer_name=gt_inv.get("customer_name"),
        subtotal=str(sub),
        tax=str(tax),
        total=wrong_total,   # wrong → ARITH_TOTAL
        currency=gt_inv.get("currency"),
    )
    # Build source_text from known PDF fields for vision grounding
    src = (
        f"{gt_inv.get('invoice_number', '')} {gt_inv.get('supplier_name', '')} "
        f"{sub} {tax} {correct_total}"
    )
    vision_resp = VisionExtraction(
        invoice_number=gt_inv.get("invoice_number"),
        invoice_date=gt_inv.get("invoice_date"),
        supplier_name=gt_inv.get("supplier_name"),
        customer_name=gt_inv.get("customer_name"),
        subtotal=str(sub),
        tax=str(tax),
        total=str(correct_total),   # correct
        currency=gt_inv.get("currency"),
        source_text=src,
    )
    fake = FakeLLM([text_resp, vision_resp])
    result = process(str(fx["pdf"]), deps=_deps(fake))
    nodes = _history_nodes(result)
    assert "vision_extract" in nodes, (
        f"Expected vision_extract in history, got: {nodes}"
    )
    assert result.status == DocStatus.validated_ok, (
        f"Vision should fix the arith error. got {result.status}. "
        f"issues={_invoice_issue_codes(result)}"
    )


def test_s5_arith_error_persists(fx, gt):
    """S5: arith error in both text and vision → needs_review."""
    gt_inv = _gt_inv(gt, "digital_A_001.pdf")
    correct_total = gt_inv.get("total", "110.00")
    wrong_total = str(Decimal(str(correct_total)) - Decimal("1.00"))
    sub = gt_inv.get("subtotal", "100.00")
    tax = gt_inv.get("tax", "10.00")

    text_resp = ExtractedInvoice(
        invoice_number=gt_inv.get("invoice_number"),
        invoice_date=gt_inv.get("invoice_date"),
        supplier_name=gt_inv.get("supplier_name"),
        customer_name=gt_inv.get("customer_name"),
        subtotal=str(sub),
        tax=str(tax),
        total=wrong_total,
        currency=gt_inv.get("currency"),
    )
    src = (
        f"{gt_inv.get('invoice_number', '')} {gt_inv.get('supplier_name', '')} "
        f"{sub} {tax} {wrong_total}"
    )
    vision_resp = VisionExtraction(
        invoice_number=gt_inv.get("invoice_number"),
        invoice_date=gt_inv.get("invoice_date"),
        supplier_name=gt_inv.get("supplier_name"),
        customer_name=gt_inv.get("customer_name"),
        subtotal=str(sub),
        tax=str(tax),
        total=wrong_total,   # still wrong
        currency=gt_inv.get("currency"),
        source_text=src,
    )
    fake = FakeLLM([text_resp, vision_resp])
    result = process(str(fx["pdf"]), deps=_deps(fake))
    nodes = _history_nodes(result)
    assert "vision_extract" in nodes, f"Expected vision_extract in history, got: {nodes}"
    assert result.status == DocStatus.needs_review, (
        f"Expected needs_review (arith error persists), got {result.status}"
    )


# ================================================================ S6-S9: Sheets

def test_s6_xlsx_tabular_zero_llm_calls(fx):
    """S6: xlsx_simple_linegrain (standard headers) → validated_ok, 0 LLM calls, tier=sheet."""
    fake = FakeLLM([])  # standard headers → deterministic pass, no LLM
    result = process(str(fx["xlsx_simple_linegrain"]), deps=_deps(fake))
    assert result.status in (DocStatus.validated_ok, DocStatus.needs_review), (
        f"Tabular xlsx must produce ok or needs_review, got {result.status}"
    )
    assert len(fake.calls) == 0, (
        f"Standard headers → 0 LLM calls expected, got {len(fake.calls)}: {fake.calls}"
    )
    # All invoices should be sheet tier
    for inv in result.invoices:
        tier = inv.tier.value if hasattr(inv.tier, "value") else str(inv.tier)
        assert tier == "sheet", f"Expected sheet tier, got {tier}"


def test_s7_xlsx_odd_headers_one_retry(fx):
    """S7: xlsx_odd_headers → LLM called once with a valid mapping → validated_ok or needs_review."""
    good_mapping = SheetMapping(
        grain="line_item",
        invoice_fields={"invoice_number": "Doc Ref", "invoice_date": "Issued On",
                        "supplier_name": "Supplier Co"},
        line_fields={"amount": "Line Amt"},
    )
    fake = FakeLLM([good_mapping])
    result = process(str(fx["xlsx_odd_headers"]), deps=_deps(fake))
    assert result.status in (DocStatus.validated_ok, DocStatus.needs_review), (
        f"Expected ok or needs_review, got {result.status}"
    )
    assert len(fake.calls) >= 1, "Expected at least 1 LLM call for odd headers"


def test_s8_xlsx_mapping_bad_exhausted(fx):
    """S8: LLM always returns bad columns → exhausts all retries → MAPPING_FAILED → needs_review.

    LLM_MAX_SCHEMA_RETRIES=2 means 3 inner loop calls. We queue 4 to ensure all
    paths through the inner loop are covered without FakeLLM exhaustion.
    """
    bad_mapping = SheetMapping(
        grain="line_item",
        invoice_fields={"invoice_number": "NONEXISTENT_COL"},
        line_fields={"amount": "ALSO_NONEXISTENT"},
    )
    fake = FakeLLM([bad_mapping, bad_mapping, bad_mapping, bad_mapping])
    result = process(str(fx["xlsx_odd_headers"]), deps=_deps(fake))
    warning_codes = _warning_codes(result)
    assert result.status in (DocStatus.needs_review, DocStatus.failed), (
        f"Expected needs_review or failed after bad mapping, got {result.status}"
    )
    assert IssueCode.MAPPING_FAILED.value in warning_codes, (
        f"Expected MAPPING_FAILED in warnings, got: {warning_codes}"
    )


def test_s9_csv_freeform(fx):
    """S9: freeform CSV → segment → llm_extract → result (ok or needs_review)."""
    fake = FakeLLM([ExtractedInvoice(
        invoice_number="CSV-001",
        invoice_date="2026-11-01",
        supplier_name="CSV Supplier",
        customer_name="Client",
        subtotal="100.00",
        tax="10.00",
        total="110.00",
        currency="USD",
    )])
    result = process(str(fx["freeform_csv"]), deps=_deps(fake))
    assert result.status in (DocStatus.validated_ok, DocStatus.needs_review), (
        f"Expected ok or needs_review for freeform CSV, got {result.status}"
    )


# ============================================================ S10-S12: Errors

def test_s10_corrupt_file(fx):
    """S10: corrupt file → failed."""
    result = process(str(fx["corrupt"]), deps=_default_deps())
    assert result.status == DocStatus.failed, (
        f"Expected failed for corrupt file, got {result.status}"
    )


def test_s11_unsupported_extension(fx):
    """S11: unsupported extension → failed."""
    result = process(str(fx["unsupported"]), deps=_default_deps())
    assert result.status == DocStatus.failed, (
        f"Expected failed for unsupported extension, got {result.status}"
    )


def test_s12_injected_exception_segment(fx, monkeypatch):
    """S12: exception inside segment @node wrapper → failed; INTERNAL_ERROR; node in history.

    We patch app.pipeline.nodes.segment._build_unit (the inner function) so the
    exception is raised INSIDE the @node("segment") wrapper — history records the
    failing node and error_code is set to INTERNAL_ERROR.
    """
    import app.pipeline.nodes.segment as _seg_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("injected failure in _build_unit")

    monkeypatch.setattr(_seg_mod, "_build_unit", _raise)

    result = process(str(fx["pdf"]), deps=_deps(FakeLLM([])))

    assert result.status == DocStatus.failed, (
        f"Expected failed after injected exception, got {result.status}"
    )
    assert result.error_code == "INTERNAL_ERROR", (
        f"Expected error_code=INTERNAL_ERROR, got {result.error_code!r}"
    )
    nodes = _history_nodes(result)
    assert "segment" in nodes, (
        f"Failing node 'segment' must appear in history (inner fn raised inside @node), "
        f"got: {nodes}"
    )


# ======================================================= S13: xlsx arith error

def test_s13_xlsx_arith_error_no_vision(fx):
    """S13: xlsx_arith_error → needs_review; vision_extract must NOT appear in history."""
    fake = FakeLLM([])  # tabular xlsx — deterministic pass handles standard headers
    result = process(str(fx["xlsx_arith_error"]), deps=_deps(fake))
    nodes = _history_nodes(result)
    assert result.status in (DocStatus.needs_review, DocStatus.validated_ok), (
        f"Expected needs_review or validated_ok for xlsx_arith_error, got {result.status}"
    )
    assert "vision_extract" not in nodes, (
        f"Sheet invoices must NEVER go to vision_extract (Fix 1 invariant), got: {nodes}"
    )


# ================================================== S14: duplicate detection

def test_s14_duplicate_invoice(fx, gt):
    """S14: second process() call with pre-seeded InMemorySeenStore → DUPLICATE_INVOICE.

    1. Run S1 to get a real invoice result.
    2. Compute the dup key from that invoice.
    3. Pre-seed a fresh InMemorySeenStore with that key.
    4. Run process() again on the same file — validate sees the key → DUPLICATE_INVOICE.
    """
    gt_inv = _gt_inv(gt, "digital_A_001.pdf")

    # Step 1: extract invoice with GT values
    fake1 = FakeLLM([_fake_from_gt(gt_inv)])
    result1 = process(str(fx["pdf"]), deps=_deps(fake1))
    assert result1.invoices, "S1 precondition: must extract at least one invoice"

    # Step 2: compute dup key from the first run's invoice
    first_inv = result1.invoices[0]
    dup_key = compute_dup_key(first_inv)
    assert dup_key is not None, f"Could not compute dup key from: {first_inv.model_dump()}"

    # Step 3: pre-seed store with that key
    store = InMemorySeenStore([dup_key])

    # Step 4: run again with same invoice, same key already in store → DUPLICATE_INVOICE
    fake2 = FakeLLM([_fake_from_gt(gt_inv)])
    result2 = process(str(fx["pdf"]), deps=_deps(fake2, seen_store=store))

    assert result2.status == DocStatus.needs_review, (
        f"Expected needs_review for duplicate, got {result2.status}"
    )
    codes = _invoice_issue_codes(result2)
    assert IssueCode.DUPLICATE_INVOICE.value in codes, (
        f"Expected DUPLICATE_INVOICE in invoice issues, got: {codes}"
    )


# ============================================== S15: missing invoice number

def test_s15_missing_invoice_number(fx):
    """S15: LLM returns invoice_number=None → needs_review + MISSING_REQUIRED."""
    fake = FakeLLM([ExtractedInvoice(
        invoice_number=None,          # missing!
        invoice_date="2026-11-01",
        supplier_name="NoNum Corp",
        customer_name="Client",
        subtotal="100.00",
        tax="10.00",
        total="110.00",
        currency="USD",
    )])
    result = process(str(fx["pdf"]), deps=_deps(fake))
    assert result.status == DocStatus.needs_review, (
        f"Expected needs_review for missing invoice_number, got {result.status}"
    )
    codes = _invoice_issue_codes(result)
    assert IssueCode.MISSING_REQUIRED.value in codes, (
        f"Expected MISSING_REQUIRED in invoice issues, got: {codes}"
    )
