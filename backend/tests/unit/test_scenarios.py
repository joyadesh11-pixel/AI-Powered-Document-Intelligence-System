"""Dry-run scenario tests.

Covers all 13 scenarios (original 12 + scenario 13 from Fix 1).
Intake now uses real file I/O; downstream nodes still use sim switches (removed in M7).

Scenarios 6–8 and 13 use real sheet fixtures with FakeLLM; the rest use sim switches.
Each scenario maps to a fixture file via the session-scoped `fx` dict in conftest.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.pipeline.graph import process, _default_deps
from app.pipeline.state import Deps
from app.pipeline.config import get_config
from app.schemas.models import DocStatus, IssueCode, SheetMapping, ExtractedInvoice, LineItemLLM
from tests.fakes import FakeLLM


# (label, file_key, sim, expected_status, assert_no_node)
# file_key is looked up in the fx fixture at test time.
# NOTE: sheet scenarios 6–8 and 13 are separate functions below.
SCENARIOS = [
    ("digital PDF, clean",
     "pdf", {}, DocStatus.validated_ok, None),

    # Scenario 2 moved to test_scenario_2_scanned_image (requires FakeLLM + real OCR).

    ("mixed PDF + low-conf OCR unit",
     "mixed", {"units": 2, "low_conf_units": [1]},
     DocStatus.validated_ok, None),

    ("arith error fixed by vision",
     "pdf", {"arith_fail_units": [0]}, DocStatus.validated_ok, None),

    ("arith error persists",
     "pdf", {"persistent_fail_units": [0]}, DocStatus.needs_review, None),

    ("csv free-form",
     "freeform_csv", {"freeform": True}, DocStatus.validated_ok, None),

    ("corrupt file",
     "corrupt", {}, DocStatus.failed, None),

    ("unsupported .docx",
     "unsupported", {}, DocStatus.failed, None),

    ("exception inside segment",
     "pdf", {"raise_in": "segment"}, DocStatus.failed, None),
]


@pytest.mark.parametrize(
    "label,file_key,sim,expected_status,assert_no_node",
    SCENARIOS,
    ids=[s[0] for s in SCENARIOS],
)
def test_scenario(label, file_key, sim, expected_status, assert_no_node, fx, gt):
    file_path = str(fx[file_key])
    filename = Path(file_path).name
    full_sim = {**sim, "gt_invoices": {
        filename: (gt.get(filename) or {}).get("invoices", [])
    }}
    result = process(file_path, deps=_default_deps(), _sim=full_sim)
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


# ---- Scenario 2: scanned image with real image_prep + ocr → validated_ok ----

def test_scenario_2_scanned_image(fx, gt):
    """Scenario 2: scan_clean.png → real OCR extracts text → LLM extracts invoice → validated_ok.

    Uses scan_clean.png (known fields: SCAN-0001, total 99.00) with a FakeLLM that
    returns the matching ExtractedInvoice so validate passes.
    """
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
    deps = Deps(config=get_config(), llm=fake, seen_store=None, mapping_cache=None)
    result = process(file_path, deps=deps)
    assert result.status == DocStatus.validated_ok, (
        f"[scenario 2] expected validated_ok, got {result.status!r}\n"
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
    filename = Path(file_path).name
    full_sim = {"gt_invoices": {filename: []}}
    result = process(file_path, deps=deps, _sim=full_sim)
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
    """Correct mapping for xlsx_odd_headers fixture."""
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
    """Same as correct but with wrong date format → parse-rate failure → retry."""
    m = _odd_headers_correct()
    return SheetMapping(**{**m.model_dump(), "date_format": "%m/%d/%Y"})


def test_scenario_7_mapping_bad_once(fx, gt):
    """Scenario 7: odd-headers xlsx — first LLM pass has wrong date format,
    second pass corrects it → validated_ok, exactly 2 LLM calls."""
    file_path = str(fx["xlsx_odd_headers"])
    fake = FakeLLM([_odd_headers_wrong_date(), _odd_headers_correct()])
    deps = Deps(config=None, llm=fake, seen_store=None, mapping_cache=None)
    filename = Path(file_path).name
    full_sim = {"gt_invoices": {filename: []}}
    result = process(file_path, deps=deps, _sim=full_sim)
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
    filename = Path(file_path).name
    full_sim = {"gt_invoices": {filename: []}}
    result = process(file_path, deps=deps, _sim=full_sim)
    assert result.status == DocStatus.needs_review, (
        f"[scenario 8] expected needs_review, got {result.status!r}\n"
        f"history: {[h['node'] for h in result.history]}"
    )
    codes = [w.code for w in result.warnings]
    assert IssueCode.MAPPING_FAILED in codes, (
        f"[scenario 8] expected MAPPING_FAILED in warnings, got {codes}"
    )


# ---- Full-graph ghost-column exhaustion: sheet_map column-validation loop → MAPPING_FAILED ----

def _odd_headers_ghost_col() -> SheetMapping:
    """A SheetMapping for xlsx_odd_headers where invoice_number maps to a nonexistent column.
    This passes Pydantic validation but fails sheet_map's column-existence check → retry loop."""
    m = _odd_headers_correct()
    fields = dict(m.invoice_fields)
    fields["invoice_number"] = "GHOST_COL"
    return SheetMapping(**{**m.model_dump(), "invoice_fields": fields})


def test_graph_ghost_column_exhaustion_three_llm_calls(fx, gt):
    """Full-graph: LLM_MAX_SCHEMA_RETRIES=2 means 3 attempts (0,1,2).
    All 3 return a mapping with a ghost column → sheet_map returns mapping=None,
    mapping_retries=MAPPING_MAX_RETRIES+1 → finalize emits MAPPING_FAILED.
    Asserts: calls==3, status==needs_review, MAPPING_FAILED in warnings, 0 invoices."""
    from app.pipeline.config import get_config
    cfg = get_config()
    n_attempts = cfg.LLM_MAX_SCHEMA_RETRIES + 1  # 3
    file_path = str(fx["xlsx_odd_headers"])
    bad = _odd_headers_ghost_col()
    fake = FakeLLM([bad] * n_attempts)
    deps = Deps(config=None, llm=fake, seen_store=None, mapping_cache=None)
    filename = Path(file_path).name
    full_sim = {"gt_invoices": {filename: []}}
    result = process(file_path, deps=deps, _sim=full_sim)
    assert len(fake.calls) == n_attempts, (
        f"Expected {n_attempts} LLM calls (column-validation exhaustion), got {len(fake.calls)}"
    )
    assert result.status == DocStatus.needs_review, (
        f"Expected needs_review after ghost-column exhaustion, got {result.status!r}\n"
        f"history: {[h['node'] for h in result.history]}"
    )
    codes = [w.code for w in result.warnings]
    assert IssueCode.MAPPING_FAILED in codes, (
        f"Expected MAPPING_FAILED in warnings, got {codes}"
    )
    assert len(result.invoices) == 0, (
        f"Expected 0 invoices after exhaustion, got {len(result.invoices)}"
    )


# ---- Scenario 13: xlsx arith error → needs_review, no vision calls ----

def test_scenario_13_xlsx_arith_error_no_vision(fx, gt):
    """Scenario 13: xlsx with arithmetic error → needs_review; vision_extract never runs."""
    file_path = str(fx["xlsx_arith_error"])
    fake = FakeLLM([])
    deps = Deps(config=None, llm=fake, seen_store=None, mapping_cache=None)
    filename = Path(file_path).name
    full_sim = {"gt_invoices": {filename: []}}
    result = process(file_path, deps=deps, _sim=full_sim)
    assert result.status == DocStatus.needs_review, (
        f"[scenario 13] expected needs_review, got {result.status!r}\n"
        f"warnings: {[w.code for w in result.warnings]}\n"
        f"history: {[h['node'] for h in result.history]}"
    )
    visited = [h["node"] for h in result.history]
    assert "vision_extract" not in visited, (
        f"[scenario 13] vision_extract must not run, history: {visited}"
    )
    assert len(fake.calls) == 0, (
        f"[scenario 13] expected 0 LLM calls, got {len(fake.calls)}"
    )
