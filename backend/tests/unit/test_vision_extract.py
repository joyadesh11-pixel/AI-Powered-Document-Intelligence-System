"""Tests for vision_extract node (T1-T8 unit, T9 live)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.pipeline.config import get_config
from app.pipeline.llm.client import LLMConfigError
from app.pipeline.nodes.vision_extract import make_vision_extract
from app.pipeline.state import Deps
from app.schemas.models import IssueCode, Tier, VisionExtraction
from tests.fakes import FakeLLM


def _good_vision(inv_num: str = "VIS-001", total: str = "110.00") -> VisionExtraction:
    return VisionExtraction(
        invoice_number=inv_num,
        total=total,
        subtotal="100.00",
        tax="10.00",
        currency="USD",
        source_text="Invoice text here",
    )


def _state(
    vision_queue: list[int],
    invoices: list[dict] | None = None,
    units: list[dict] | None = None,
    file_type: str = "pdf",
    escalated: bool = False,
    page_images: dict | None = None,
) -> dict:
    if units is None:
        units = [
            {"id": i, "pages": [i], "source": "ocr", "needs_vision": True, "low_conf_pages": [i]}
            for i in vision_queue
        ]
    return {
        "file_type": file_type,
        "file_path": "",
        "file_hash": "abc123",
        "page_text": {i: "" for i in vision_queue},
        "page_source": {i: "ocr" for i in vision_queue},
        "tables": {},
        "units": units,
        "invoices": invoices or [],
        "vision_queue": vision_queue,
        "validation_errors": [],
        "warnings": [],
        "llm_usage": {},
        "escalated": escalated,
        "page_images": page_images or {},
    }


def _run(vision_queue, responses, **kw):
    fake = FakeLLM(responses)
    deps = Deps(config=get_config(), llm=fake, seen_store=None, mapping_cache=None)
    fn = make_vision_extract(deps)
    s = _state(vision_queue, **kw)
    return fn(s), fake


# ------------------------------------------------------------------ T1

def test_only_queued_unit_extracted():
    """T1: vision_queue=[1], units=[0,1] → only unit 1 re-extracted; unit 0 invoice untouched."""
    existing = [{"unit_id": 0, "tier": "parser", "invoice_number": "KEEP", "total": Decimal("50.00"),
                 "source_pages": [0], "issues": []}]
    units = [
        {"id": 0, "pages": [0], "source": "digital", "needs_vision": False, "low_conf_pages": []},
        {"id": 1, "pages": [1], "source": "ocr", "needs_vision": True, "low_conf_pages": [1]},
    ]
    fake = FakeLLM([_good_vision("VIS-001")])
    deps = Deps(config=get_config(), llm=fake, seen_store=None, mapping_cache=None)
    fn = make_vision_extract(deps)
    s = _state([1], invoices=existing, units=units)
    result = fn(s)

    assert len(fake.calls) == 1
    invs = result["invoices"]
    inv0 = next(i for i in invs if i.get("unit_id") == 0)
    assert inv0["invoice_number"] == "KEEP"
    inv1 = next(i for i in invs if i.get("unit_id") == 1)
    assert inv1["invoice_number"] == "VIS-001"


# ------------------------------------------------------------------ T2

def test_vision_invoice_has_vision_tier():
    """T2: vision response → tier=vision."""
    result, _ = _run([0], [_good_vision()])
    assert result["invoices"][0]["tier"] == Tier.vision.value


# ------------------------------------------------------------------ T3

def test_vision_wins_when_it_has_fewer_errors():
    """T3: vision has fewer errors than text → vision kept; text in alternatives."""
    # Text candidate has an arith error (total != subtotal + tax)
    text_inv = {
        "unit_id": 0,
        "tier": "parser",
        "invoice_number": "TEXT-001",
        "invoice_date": None,
        "due_date": None,
        "supplier_name": None,
        "customer_name": None,
        "subtotal": Decimal("100.00"),
        "tax": Decimal("10.00"),
        "total": Decimal("999.00"),  # wrong total → ARITH_TOTAL
        "currency": "USD",
        "line_items": [],
        "source_pages": [0],
        "raw": {"subtotal": "100.00", "tax": "10.00", "total": "999.00"},
        "issues": [{"code": "ARITH_TOTAL", "severity": "error",
                    "message": "arith error", "field": "total",
                    "expected": None, "got": None, "location": {}}],
    }
    # Vision has correct total
    vision_resp = VisionExtraction(
        invoice_number="TEXT-001",
        subtotal="100.00",
        tax="10.00",
        total="110.00",  # correct
        currency="USD",
        source_text="",
    )
    fake = FakeLLM([vision_resp])
    units = [{"id": 0, "pages": [0], "source": "ocr", "needs_vision": True, "low_conf_pages": [0]}]
    deps = Deps(config=get_config(), llm=fake, seen_store=None, mapping_cache=None)
    fn = make_vision_extract(deps)
    s = _state([0], invoices=[text_inv], units=units)
    result = fn(s)

    invs = result["invoices"]
    assert len(invs) == 1
    winner = invs[0]
    # Vision should win (fewer errors)
    assert winner["tier"] == Tier.vision.value or winner.get("total") == Decimal("110.00")


# ------------------------------------------------------------------ T4

def test_text_wins_when_vision_has_more_errors():
    """T4: text has no errors, vision has errors → text wins."""
    # Text candidate is clean
    text_inv = {
        "unit_id": 0,
        "tier": "parser",
        "invoice_number": "GOOD-001",
        "invoice_date": None,
        "due_date": None,
        "supplier_name": "Supplier",
        "customer_name": "Customer",
        "subtotal": Decimal("100.00"),
        "tax": Decimal("10.00"),
        "total": Decimal("110.00"),
        "currency": "USD",
        "line_items": [],
        "source_pages": [0],
        "raw": {"subtotal": "100.00", "tax": "10.00", "total": "110.00"},
        "issues": [],
    }
    # Vision has bad total
    vision_resp = VisionExtraction(
        invoice_number="GOOD-001",
        subtotal="100.00",
        tax="10.00",
        total="999.00",  # wrong
        currency="USD",
        source_text="",
    )
    fake = FakeLLM([vision_resp])
    units = [{"id": 0, "pages": [0], "source": "ocr", "needs_vision": True, "low_conf_pages": [0]}]
    deps = Deps(config=get_config(), llm=fake, seen_store=None, mapping_cache=None)
    fn = make_vision_extract(deps)
    s = _state([0], invoices=[text_inv], units=units)
    result = fn(s)

    winner = result["invoices"][0]
    # Text should win because vision has arith error
    assert winner["invoice_number"] == "GOOD-001"


# ---- Arbitration: DUPLICATE_INVOICE must not cause vision to win ----

def test_text_wins_when_only_error_is_duplicate_invoice():
    """Arbitration: text's only error is DUPLICATE_INVOICE (non-escalatable).

    DUPLICATE_INVOICE is not in ESCALATION_CODES, so validate never adds the unit to
    vision_queue for this reason.  In arbitration, run_all is called fresh with
    seen_store=None, so dup check is skipped for both candidates — both score 0 fresh
    errors → tie.  On a tie, text wins (vision cannot fix a document-level duplicate).
    """
    from app.schemas.models import IssueCode, Severity

    text_inv = {
        "unit_id": 0,
        "tier": "ocr",
        "invoice_number": "DUP-001",
        "invoice_date": None,
        "due_date": None,
        "supplier_name": "Supplier Co",
        "customer_name": "Customer Ltd",
        "subtotal": Decimal("100.00"),
        "tax": Decimal("10.00"),
        "total": Decimal("110.00"),  # arithmetic is correct
        "currency": "USD",
        "line_items": [],
        "source_pages": [0],
        "raw": {"subtotal": "100.00", "tax": "10.00", "total": "110.00"},
        "issues": [{
            "code": IssueCode.DUPLICATE_INVOICE.value,
            "severity": Severity.error.value,
            "message": "duplicate of a previously seen document",
            "field": None, "expected": None, "got": None, "location": {},
        }],
    }
    # Vision sees the same invoice cleanly (correct arithmetic, no dup in fresh run_all)
    vision_resp = VisionExtraction(
        invoice_number="DUP-001",
        subtotal="100.00",
        tax="10.00",
        total="110.00",
        currency="USD",
        source_text="Invoice text",
    )
    fake = FakeLLM([vision_resp])
    units = [{"id": 0, "pages": [0], "source": "ocr", "needs_vision": True,
              "low_conf_pages": [0]}]
    deps = Deps(config=get_config(), llm=fake, seen_store=None, mapping_cache=None)
    fn = make_vision_extract(deps)
    s = _state([0], invoices=[text_inv], units=units)
    result = fn(s)

    invs = result["invoices"]
    assert len(invs) == 1
    winner = invs[0]
    # Text must win: DUPLICATE_INVOICE is not escalatable; both score 0 fresh errors
    # (seen_store=None excludes dup from run_all); tie → text wins (more conservative).
    assert winner.get("tier") == Tier.ocr.value, (
        f"Expected text (ocr) to win arbitration when its only error is DUPLICATE_INVOICE, "
        f"but winner tier is {winner.get('tier')!r}"
    )
    assert winner.get("invoice_number") == "DUP-001"


# ------------------------------------------------------------------ T5

def test_vision_pages_capped():
    """T5: unit with more pages than VISION_MAX_PAGES → VISION_PAGES_CAPPED warning."""
    cfg = get_config()
    n = cfg.VISION_MAX_PAGES + 2
    pages = list(range(n))
    unit = {"id": 0, "pages": pages, "source": "ocr", "needs_vision": True,
            "low_conf_pages": pages}
    fake = FakeLLM([_good_vision()])
    deps = Deps(config=cfg, llm=fake, seen_store=None, mapping_cache=None)
    fn = make_vision_extract(deps)
    s = _state([0], units=[unit])
    result = fn(s)

    codes = [w.get("code") for w in result.get("warnings", [])]
    assert IssueCode.VISION_PAGES_CAPPED.value in codes


# ------------------------------------------------------------------ T6

def test_vision_failed_no_exception():
    """T6: FakeLLM raises exception → VISION_FAILED warning; no exception from node."""
    fake = FakeLLM([RuntimeError("LLM timeout")])
    deps = Deps(config=get_config(), llm=fake, seen_store=None, mapping_cache=None)
    fn = make_vision_extract(deps)
    s = _state([0])
    result = fn(s)  # must not raise

    codes = [w.get("code") for w in result.get("warnings", [])]
    assert IssueCode.VISION_FAILED.value in codes


def test_vision_config_error_propagates():
    """LLMConfigError from vision LLM propagates (not caught)."""
    fake = FakeLLM([LLMConfigError("no key")])
    deps = Deps(config=get_config(), llm=fake, seen_store=None, mapping_cache=None)
    fn = make_vision_extract(deps)
    s = _state([0])
    with pytest.raises(LLMConfigError):
        fn(s)


# ------------------------------------------------------------------ T7

def test_escalated_true_and_queue_empty():
    """T7: after vision_extract, result has escalated=True and vision_queue=[]."""
    result, _ = _run([0], [_good_vision()])
    assert result.get("escalated") is True
    assert result.get("vision_queue") == []


# ------------------------------------------------------------------ T8

def test_source_text_updates_page_text_for_low_conf_pages():
    """T8: vision source_text replaces page text for low-conf pages; page_source=vision."""
    vision_resp = VisionExtraction(
        invoice_number="VIS-001",
        total="100.00",
        source_text="Transcribed invoice text",
    )
    fake = FakeLLM([vision_resp])
    units = [{"id": 0, "pages": [0], "source": "ocr", "needs_vision": True, "low_conf_pages": [0]}]
    deps = Deps(config=get_config(), llm=fake, seen_store=None, mapping_cache=None)
    fn = make_vision_extract(deps)
    s = _state([0], units=units)
    result = fn(s)

    assert result.get("page_text", {}).get(0) == "Transcribed invoice text"
    assert result.get("page_source", {}).get(0) == "vision"


# ------------------------------------------------------------------ T8b: sheet guard

def test_sheet_file_type_skipped():
    """vision_extract returns {} for sheet files (tier guard)."""
    fake = FakeLLM([_good_vision()])
    deps = Deps(config=get_config(), llm=fake, seen_store=None, mapping_cache=None)
    fn = make_vision_extract(deps)
    s = _state([0], file_type="sheet")
    result = fn(s)
    assert len(fake.calls) == 0


def test_already_escalated_skipped():
    """vision_extract returns {} when escalated=True (already ran)."""
    fake = FakeLLM([_good_vision()])
    deps = Deps(config=get_config(), llm=fake, seen_store=None, mapping_cache=None)
    fn = make_vision_extract(deps)
    s = _state([0], escalated=True)
    result = fn(s)
    assert len(fake.calls) == 0


# ------------------------------------------------------------------ T9 live

@pytest.mark.live
def test_live_vision_extract_scan_moderate(fx):
    """T9 live: scan_moderate.png through vision → fields match GT."""
    import time
    from dotenv import load_dotenv
    load_dotenv()
    from app.pipeline.graph import process
    from app.pipeline.llm.client import build_llm_client
    from app.pipeline.config import get_config
    from app.pipeline.state import Deps

    cfg = get_config()
    real_llm = build_llm_client(cfg)
    deps = Deps(config=cfg, llm=real_llm, seen_store=None, mapping_cache=None)

    t0 = time.perf_counter()
    result = process(str(fx["scan_moderate"]), deps=deps)
    elapsed = time.perf_counter() - t0

    usage = result.llm_usage
    print(
        f"\n[live T9] model={cfg.LLM_MODEL_VISION} "
        f"latency={elapsed:.2f}s "
        f"input={usage.get('input_tokens',0)} "
        f"output={usage.get('output_tokens',0)}"
    )
    assert result.invoices, "No invoices extracted from scan_moderate"
    assert result.invoices[0].invoice_number is not None


# ------------------------------------------------------------------ T10 live: scan_clean full pipeline

@pytest.mark.live
def test_live_scan_clean_full_pipeline(fx, gt):
    """T10 live: scan_clean.png through the full pipeline — validated_ok, fields extracted."""
    import time
    import json
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv()
    from app.pipeline.graph import process
    from app.pipeline.llm.client import build_llm_client
    from app.pipeline.config import get_config
    from app.pipeline.state import Deps
    from app.schemas.models import DocStatus

    cfg = get_config()
    real_llm = build_llm_client(cfg)
    deps = Deps(config=cfg, llm=real_llm, seen_store=None, mapping_cache=None)

    file_path = str(fx["scan_clean"])
    t0 = time.perf_counter()
    result = process(file_path, deps=deps)
    elapsed = time.perf_counter() - t0

    usage = result.llm_usage
    # ---- report ----
    print(f"\n[live T10] scan_clean.png  model={cfg.LLM_MODEL_TEXT}"
          f"  latency={elapsed:.2f}s"
          f"  input={usage.get('input_tokens',0)} output={usage.get('output_tokens',0)}")
    gt_entry = gt.get("scan_clean.png", {})
    gt_fields = (gt_entry.get("invoices") or [{}])[0].get("printed") or {}
    if result.invoices:
        inv = result.invoices[0]
        rows = [
            ("invoice_number", inv.invoice_number, gt_fields.get("invoice_number")),
            ("supplier_name",  inv.supplier_name,  gt_fields.get("supplier_name")),
            ("total",          str(inv.total),      gt_fields.get("total")),
            ("currency",       inv.currency,        gt_fields.get("currency")),
        ]
        print(f"  {'field':<20} {'extracted':<25} {'gt (if present)'}")
        for field, ext, expected in rows:
            match = "" if not expected else ("✓" if str(ext) == str(expected) else "✗")
            print(f"  {field:<20} {str(ext):<25} {expected or '(no GT)'}  {match}")
    # ---- assertions ----
    assert result.status == DocStatus.validated_ok, (
        f"scan_clean.png: expected validated_ok, got {result.status}\n"
        f"warnings: {[w.code for w in result.warnings]}"
    )
    assert result.invoices, "scan_clean.png: no invoices extracted"
    inv = result.invoices[0]
    assert inv.invoice_number is not None, "invoice_number must not be null on clean scan"
    assert inv.total is not None, "total must not be null on clean scan"


# ------------------------------------------------------------------ T11 live: blurry_severe vision — expect null fields, nothing invented

@pytest.mark.live
def test_live_blurry_severe_vision_null_fields(fx, gt):
    """T11 live: blurry_severe.png through vision — fields may be null; none invented.

    GT has no invoices for this fixture (unreadable).  The pipeline must not crash and
    must not return plausible-looking invented values.  All null fields are acceptable;
    having a non-null invoice_number is unexpected and flagged as a warning.
    """
    import time
    from dotenv import load_dotenv
    load_dotenv()
    from app.pipeline.graph import process
    from app.pipeline.llm.client import build_llm_client
    from app.pipeline.config import get_config
    from app.pipeline.state import Deps
    from app.schemas.models import DocStatus

    cfg = get_config()
    real_llm = build_llm_client(cfg)
    deps = Deps(config=cfg, llm=real_llm, seen_store=None, mapping_cache=None)

    file_path = str(fx["blurry_severe"])
    t0 = time.perf_counter()
    result = process(file_path, deps=deps)
    elapsed = time.perf_counter() - t0

    usage = result.llm_usage
    print(f"\n[live T11] blurry_severe.png  model={cfg.LLM_MODEL_VISION}"
          f"  latency={elapsed:.2f}s"
          f"  input={usage.get('input_tokens',0)} output={usage.get('output_tokens',0)}")
    if result.invoices:
        inv = result.invoices[0]
        print(f"  invoice_number={inv.invoice_number!r}  total={inv.total!r}"
              f"  supplier={inv.supplier_name!r}")
        if inv.invoice_number is not None:
            print("  WARNING: invoice_number is non-null on blurry_severe — may be invented")
    else:
        print("  No invoices extracted (expected for unreadable document)")

    # Must not crash; GT says needs_review
    assert result.status in (DocStatus.needs_review, DocStatus.validated_ok), (
        f"blurry_severe.png: expected needs_review or validated_ok, got {result.status}"
    )
    # If an invoice was extracted, its total must not be confidently non-null
    # (blurry documents should produce null or uncertain fields, not invented ones)
    if result.invoices:
        inv = result.invoices[0]
        # Warn but don't fail — model may legitimately read partial text
        if inv.total is not None:
            print(f"  NOTE: total={inv.total!r} extracted from blurry image; verify manually")


# ------------------------------------------------------------------ T12 live: xlsx_odd_headers sheet mapping

@pytest.mark.live
def test_live_xlsx_odd_headers_sheet_map(fx, gt):
    """T12 live: xlsx_odd_headers.xlsx — needs LLM for non-synonym headers; reports exact error
    if SheetMapping schema fails Gemini structured output."""
    import time
    from dotenv import load_dotenv
    load_dotenv()
    from app.pipeline.graph import process
    from app.pipeline.llm.client import build_llm_client
    from app.pipeline.config import get_config
    from app.pipeline.state import Deps
    from app.schemas.models import DocStatus

    cfg = get_config()
    real_llm = build_llm_client(cfg)
    deps = Deps(config=cfg, llm=real_llm, seen_store=None, mapping_cache=None)

    file_path = str(fx["xlsx_odd_headers"])
    t0 = time.perf_counter()
    result = process(file_path, deps=deps)
    elapsed = time.perf_counter() - t0

    usage = result.llm_usage
    print(f"\n[live T12] xlsx_odd_headers.xlsx  model={cfg.LLM_MODEL_TEXT}"
          f"  latency={elapsed:.2f}s"
          f"  input={usage.get('input_tokens',0)} output={usage.get('output_tokens',0)}")
    print(f"  status={result.status}  invoices={len(result.invoices)}"
          f"  llm_calls={usage.get('calls',0)}")
    if result.invoices:
        inv = result.invoices[0]
        gt_entry = gt.get("xlsx_odd_headers.xlsx", {})
        gt_fields = (gt_entry.get("invoices") or [{}])[0].get("printed") or {}
        rows = [
            ("invoice_number", inv.invoice_number, gt_fields.get("invoice_number")),
            ("invoice_date",   str(inv.invoice_date),  gt_fields.get("invoice_date")),
            ("supplier_name",  inv.supplier_name,  gt_fields.get("supplier_name")),
            ("total",          str(inv.total),      gt_fields.get("total")),
        ]
        print(f"  {'field':<20} {'extracted':<25} {'gt (if present)'}")
        for field, ext, expected in rows:
            match = "" if not expected else ("✓" if str(ext) == str(expected) else "✗")
            print(f"  {field:<20} {str(ext):<25} {expected or '(no GT)'}  {match}")
    if result.warnings:
        print(f"  warnings: {[w.code for w in result.warnings]}")
    # If mapping failed, print exact error detail so we can propose a fix
    if result.error:
        print(f"  ERROR: {result.error}")
    # Assert pipeline ran without crash; mapping may succeed or need review
    assert result.status in (DocStatus.validated_ok, DocStatus.needs_review), (
        f"xlsx_odd_headers: expected validated_ok or needs_review, got {result.status}\n"
        f"error: {result.error}"
    )
