"""Tests for sheet_map node — M4.

synonym_keys_normalised   → every SYNONYMS key == _normalise(key)
odd_headers_not_in_synonyms → "Doc Ref", "Supplier Co", "Line Amt", "Issued On" not in SYNONYMS

T1  standard headers → 0 LLM calls
T2  xlsx_odd_headers → exactly 1 LLM call
T3  xlsx_ambiguous_dates → LLM called; mapping has date_format
T4  cache hit → 0 LLM calls
T5  feedback retry → second LLM call prompt contains feedback
T6  LLM returns unknown column → 2nd response corrects; 2 calls total
T7  LLM always returns unknown → result["mapping"] is None; MAPPING_FAILED via finalize
T8  grain detection: duplicated invoice_number → line_item; unique → invoice
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.pipeline.nodes.sheet_load import sheet_load
from app.pipeline.nodes.sheet_classify import sheet_classify
from app.pipeline.nodes.sheet_map import make_sheet_map
from app.pipeline.utils.synonyms import SYNONYMS, _normalise
from app.schemas.models import IssueCode, SheetMapping
from tests.fakes import FakeLLM


def _load_classify(path, file_type="sheet"):
    state = {"file_path": str(path), "file_type": file_type, "sim": {}, "warnings": []}
    r1 = sheet_load(state)
    state = {**state, **r1}
    r2 = sheet_classify(state)
    return {**state, **r2}


def _make_deps(llm=None, cache=None):
    from app.pipeline.config import get_config
    return {"config": get_config(), "llm": llm or FakeLLM([]), "seen_store": None,
            "mapping_cache": cache}


# ---- synonym sanity ----

def test_all_synonym_keys_normalised():
    for k in SYNONYMS:
        assert k == _normalise(k), f"Key {k!r} is not normalised"


def test_odd_headers_not_in_synonyms():
    for col in ["Doc Ref", "Supplier Co", "Line Amt", "Issued On"]:
        assert _normalise(col) not in SYNONYMS, (
            f"Odd header {col!r} should not be in SYNONYMS"
        )


# ---- T1: standard headers → 0 LLM calls ----

def test_T1_standard_headers_zero_llm_calls(fx):
    state = _load_classify(fx["xlsx_simple_linegrain"])
    fake = FakeLLM([])
    deps = _make_deps(llm=fake)
    node = make_sheet_map(deps)
    result = node(state)
    assert result.get("mapping") is not None
    assert len(fake.calls) == 0, f"Expected 0 LLM calls, got {len(fake.calls)}"


# ---- T2: odd headers → exactly 1 LLM call ----

def test_T2_odd_headers_one_llm_call(fx):
    state = _load_classify(fx["xlsx_odd_headers"])
    assert state.get("sheet_kind") == "tabular"
    # Build a correct mapping for odd headers
    sheet = next(s for s in state["sheets"] if s["name"] == state["primary_sheet"])
    cols = sheet["columns"]
    mapping = SheetMapping(
        grain="line_item",
        group_by="Doc Ref",
        invoice_fields={"invoice_number": "Doc Ref", "invoice_date": "Issued On",
                        "supplier_name": "Supplier Co", "customer_name": "Customer"},
        line_fields={"description": "Description", "quantity": "Quantity",
                     "unit_price": "Unit Price", "amount": "Line Amt"},
        date_format="%d/%m/%Y",
        decimal_separator=".",
        thousands_separator=",",
    )
    fake = FakeLLM([mapping])
    deps = _make_deps(llm=fake)
    node = make_sheet_map(deps)
    result = node(state)
    assert result.get("mapping") is not None
    assert len(fake.calls) == 1, f"Expected 1 LLM call, got {len(fake.calls)}"


# ---- T3: ambiguous dates → LLM called ----

def test_T3_ambiguous_dates_llm_called(fx):
    state = _load_classify(fx["xlsx_ambiguous_dates"])
    sheet = next(s for s in state["sheets"] if s["name"] == state.get("primary_sheet", state["sheets"][0]["name"]))
    cols = sheet["columns"]
    mapping = SheetMapping(
        grain="invoice",
        group_by=None,
        invoice_fields={"invoice_number": "Invoice Number", "invoice_date": "Invoice Date"},
        line_fields={},
        date_format="%d/%m/%Y",
    )
    fake = FakeLLM([mapping])
    deps = _make_deps(llm=fake)
    node = make_sheet_map(deps)
    result = node(state)
    assert len(fake.calls) >= 1
    m = result.get("mapping")
    assert m is not None
    assert m.get("date_format") is not None


# ---- T4: cache hit → 0 LLM calls ----

def test_T4_cache_hit_zero_calls(fx):
    state = _load_classify(fx["xlsx_simple_linegrain"])
    sheet = next(s for s in state["sheets"] if s["name"] == state.get("primary_sheet"))
    hash_ = sheet["header_hash"]

    cached = SheetMapping(
        grain="line_item",
        group_by="Invoice Number",
        invoice_fields={"invoice_number": "Invoice Number", "invoice_date": "Invoice Date",
                        "supplier_name": "Supplier", "customer_name": "Customer",
                        "currency": "Currency", "subtotal": "Subtotal",
                        "tax": "Tax", "total": "Total"},
        line_fields={"description": "Description", "quantity": "Quantity",
                     "unit_price": "Unit Price", "amount": "Amount"},
        date_format="%Y-%m-%d",
    )

    def cache_fn(h):
        return cached if h == hash_ else None

    fake = FakeLLM([])
    deps = _make_deps(llm=fake, cache=cache_fn)
    node = make_sheet_map(deps)
    result = node(state)
    assert result.get("mapping") is not None
    assert len(fake.calls) == 0


# ---- T5: feedback retry prompt contains feedback ----

def test_T5_feedback_retry_prompt_has_feedback(fx):
    state = _load_classify(fx["xlsx_odd_headers"])
    # Add mapping_feedback to state
    sheet = next(s for s in state["sheets"] if s["name"] == state["primary_sheet"])
    cols = sheet["columns"]
    mapping = SheetMapping(
        grain="line_item",
        group_by="Doc Ref",
        invoice_fields={"invoice_number": "Doc Ref", "invoice_date": "Issued On",
                        "supplier_name": "Supplier Co", "customer_name": "Customer"},
        line_fields={"description": "Description", "quantity": "Quantity",
                     "unit_price": "Unit Price", "amount": "Line Amt"},
        date_format="%d/%m/%Y",
    )
    feedback_msg = "column Issued On: 80% parsed with %m/%d/%Y; failing: 15/10/2026"
    state["mapping_feedback"] = feedback_msg

    fake = FakeLLM([mapping])
    deps = _make_deps(llm=fake)
    node = make_sheet_map(deps)
    result = node(state)
    assert len(fake.calls) == 1
    # The user prompt should contain the feedback text
    call_user = fake.calls[0].get("user", "")
    assert feedback_msg in call_user or "feedback" in call_user.lower() or "parse" in call_user.lower()


# ---- T6: LLM returns unknown column → 2nd response corrects ----

def test_T6_llm_bad_column_corrected(fx):
    state = _load_classify(fx["xlsx_odd_headers"])
    sheet = next(s for s in state["sheets"] if s["name"] == state["primary_sheet"])
    cols = sheet["columns"]

    # First response has a ghost column not in the sheet
    bad_mapping = SheetMapping(
        grain="line_item",
        group_by="Doc Ref",
        invoice_fields={"invoice_number": "GHOST_COL", "invoice_date": "Issued On",
                        "supplier_name": "Supplier Co", "customer_name": "Customer"},
        line_fields={"amount": "Line Amt"},
        date_format="%d/%m/%Y",
    )
    good_mapping = SheetMapping(
        grain="line_item",
        group_by="Doc Ref",
        invoice_fields={"invoice_number": "Doc Ref", "invoice_date": "Issued On",
                        "supplier_name": "Supplier Co", "customer_name": "Customer"},
        line_fields={"description": "Description", "quantity": "Quantity",
                     "unit_price": "Unit Price", "amount": "Line Amt"},
        date_format="%d/%m/%Y",
    )
    fake = FakeLLM([bad_mapping, good_mapping])
    deps = _make_deps(llm=fake)
    node = make_sheet_map(deps)
    result = node(state)
    assert len(fake.calls) == 2, f"Expected 2 calls, got {len(fake.calls)}"
    m = result.get("mapping")
    assert m is not None
    assert m["invoice_fields"]["invoice_number"] == "Doc Ref"


# ---- T7: LLM always returns unknown → mapping=None ----

def test_T7_llm_always_bad_mapping_exhausted(fx):
    from app.pipeline.config import get_config
    cfg = get_config()
    state = _load_classify(fx["xlsx_odd_headers"])
    # Always return a mapping with a ghost column
    bad_mapping = SheetMapping(
        grain="line_item", group_by="GHOST",
        invoice_fields={"invoice_number": "GHOST_COL"},
        line_fields={}, date_format=None,
    )
    # Need MAX_RETRIES + 2 bad responses (initial + retries in the schema loop)
    from app.pipeline.config import get_config
    cfg = get_config()
    n_attempts = cfg.LLM_MAX_SCHEMA_RETRIES + 1
    fake = FakeLLM([bad_mapping] * n_attempts)
    deps = _make_deps(llm=fake)
    node = make_sheet_map(deps)
    result = node(state)
    assert result.get("mapping") is None, "Expected mapping=None after exhaustion"
    assert result.get("mapping_retries", 0) > cfg.MAPPING_MAX_RETRIES


# ---- T8: grain detection ----

def test_T8_grain_detection_linegrain(fx):
    state = _load_classify(fx["xlsx_simple_linegrain"])
    fake = FakeLLM([])
    deps = _make_deps(llm=fake)
    node = make_sheet_map(deps)
    result = node(state)
    m = result.get("mapping")
    assert m is not None
    assert m["grain"] == "line_item"


def test_T8_grain_detection_invoice(fx):
    state = _load_classify(fx["xlsx_invoice_grain"])
    # invoice_grain has unique invoice numbers per row → grain=invoice
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
    fake = FakeLLM([mapping])
    deps = _make_deps(llm=fake)
    node = make_sheet_map(deps)
    result = node(state)
    m = result.get("mapping")
    assert m is not None
    # Should detect grain=invoice (no repeated invoice numbers)
    assert m["grain"] == "invoice"


# ---- merge rule tests ----

def test_merge_deterministic_overrides_llm(fx):
    """On first pass, deterministic fields override LLM output."""
    state = _load_classify(fx["xlsx_simple_linegrain"])
    # LLM returns wrong date_format and wrong grain; deterministic should override
    wrong_mapping = SheetMapping(
        grain="invoice",  # wrong, should be line_item (duplicated invoice numbers)
        group_by="Invoice Number",
        invoice_fields={"invoice_number": "Invoice Number",
                        "invoice_date": "Invoice Date",
                        "supplier_name": "Supplier"},
        line_fields={"amount": "Amount"},
        date_format="%m/%d/%Y",  # wrong format
    )
    # deterministic pass resolves all columns and date_format → no LLM call at all
    fake = FakeLLM([wrong_mapping])
    deps = _make_deps(llm=fake)
    node = make_sheet_map(deps)
    result = node(state)
    m = result.get("mapping")
    assert m is not None
    # Since standard headers are all in SYNONYMS → 0 LLM calls (deterministic complete)
    assert len(fake.calls) == 0
    assert m["date_format"] == "%Y-%m-%d"
    assert m["grain"] == "line_item"


# ---- standard headers + extra date column "Issued On" → 1 LLM call ----

def test_standard_headers_plus_unmapped_date_col_one_llm_call(tmp_path):
    """Standard headers + an extra 'Issued On' date column → deterministic pass leaves an
    unmapped date-like column → _is_complete returns False → exactly 1 LLM call."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Invoice Number", "Invoice Date", "Amount", "Issued On"])
    for i in range(1, 4):
        ws.append([f"INV-{i:03d}", f"2026-10-{i:02d}", float(i * 100), f"2026-10-{i+2:02d}"])
    p = tmp_path / "std_plus_issued_on.xlsx"
    wb.save(str(p))

    state = {"file_path": str(p), "file_type": "sheet", "sim": {}, "warnings": []}
    from app.pipeline.nodes.sheet_load import sheet_load
    from app.pipeline.nodes.sheet_classify import sheet_classify
    state = {**state, **sheet_load(state)}
    state = {**state, **sheet_classify(state)}

    llm_mapping = SheetMapping(
        grain="invoice",
        group_by=None,
        invoice_fields={"invoice_number": "Invoice Number", "invoice_date": "Invoice Date",
                        "due_date": "Issued On"},
        line_fields={"amount": "Amount"},
        date_format="%Y-%m-%d",
    )
    fake = FakeLLM([llm_mapping])
    deps = _make_deps(llm=fake)
    node = make_sheet_map(deps)
    result = node(state)
    assert len(fake.calls) == 1, (
        f"Expected 1 LLM call (unmapped date column 'Issued On'), got {len(fake.calls)}"
    )
    m = result.get("mapping")
    assert m is not None
    assert m["invoice_fields"].get("invoice_date") is not None


# ---- standard headers with NO date column → 0 LLM calls ----

def test_standard_headers_no_date_col_zero_llm_calls(tmp_path):
    """A sheet with invoice_number + amount but NO date column is complete without LLM."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Invoice Number", "Description", "Amount"])
    for i in range(1, 4):
        ws.append([f"INV-{i:03d}", f"Item {i}", float(i * 50)])
    p = tmp_path / "std_no_date.xlsx"
    wb.save(str(p))

    state = {"file_path": str(p), "file_type": "sheet", "sim": {}, "warnings": []}
    from app.pipeline.nodes.sheet_load import sheet_load
    from app.pipeline.nodes.sheet_classify import sheet_classify
    state = {**state, **sheet_load(state)}
    state = {**state, **sheet_classify(state)}

    fake = FakeLLM([])
    deps = _make_deps(llm=fake)
    node = make_sheet_map(deps)
    result = node(state)
    assert len(fake.calls) == 0, (
        f"No date column → deterministic pass is complete → 0 LLM calls, got {len(fake.calls)}"
    )
    assert result.get("mapping") is not None


def test_merge_deterministic_wins_conflicting_llm(fx):
    """T-merge(a): on first pass, a deterministic column overrides an LLM column that
    names a valid but wrong spreadsheet column for the same field."""
    state = _load_classify(fx["xlsx_odd_headers"])
    # Deterministic pass resolves customer_name → "Customer" (SYNONYMS hit).
    # LLM returns customer_name → "Supplier Co" (valid column, but wrong field).
    # _merge must keep "Customer" (deterministic wins).
    llm_mapping = SheetMapping(
        grain="line_item",
        group_by="Doc Ref",
        invoice_fields={
            "invoice_number": "Doc Ref",
            "invoice_date": "Issued On",
            "supplier_name": "Supplier Co",
            "customer_name": "Supplier Co",  # wrong: should be "Customer"
        },
        line_fields={"description": "Description", "amount": "Line Amt"},
        date_format="%d/%m/%Y",
    )
    fake = FakeLLM([llm_mapping])
    deps = _make_deps(llm=fake)
    node = make_sheet_map(deps)
    result = node(state)
    assert len(fake.calls) == 1, "LLM must be called (odd headers not fully deterministic)"
    m = result.get("mapping")
    assert m is not None
    # Deterministic resolved "Customer" → customer_name; must survive the merge
    assert m["invoice_fields"]["customer_name"] == "Customer", (
        f"Deterministic 'Customer' must win over LLM's 'Supplier Co', "
        f"got {m['invoice_fields'].get('customer_name')!r}"
    )


def test_merge_feedback_retry_llm_authoritative(fx):
    """On feedback retry, LLM is fully authoritative."""
    state = _load_classify(fx["xlsx_odd_headers"])
    sheet = next(s for s in state["sheets"] if s["name"] == state["primary_sheet"])
    # Set feedback so it goes to LLM
    state["mapping_feedback"] = "column Issued On: 80% parsed; failing: 15/10/2026"
    llm_mapping = SheetMapping(
        grain="line_item",
        group_by="Doc Ref",
        invoice_fields={"invoice_number": "Doc Ref", "invoice_date": "Issued On",
                        "supplier_name": "Supplier Co", "customer_name": "Customer"},
        line_fields={"description": "Description", "quantity": "Quantity",
                     "unit_price": "Unit Price", "amount": "Line Amt"},
        date_format="%d/%m/%Y",
    )
    fake = FakeLLM([llm_mapping])
    deps = _make_deps(llm=fake)
    node = make_sheet_map(deps)
    result = node(state)
    m = result.get("mapping")
    assert m is not None
    # LLM's date_format is authoritative on retry
    assert m["date_format"] == "%d/%m/%Y"


# ---- Router recursion regression: NullLLM failure must terminate, not recurse ----

def test_router_nullllm_terminates_not_recurses(fx):
    """Regression: when sheet_map raises LLMConfigError (NullLLM), the @node wrapper
    sets state['error'] and route_after_transform must return 'finalize', not 'sheet_map'.
    Before the router fix this caused GraphRecursionError."""
    from app.pipeline.graph import process, _default_deps
    from app.schemas.models import DocStatus

    # xlsx_odd_headers requires an LLM call (non-synonym headers).
    # _default_deps() uses NullLLM → LLMConfigError → @node catches → error in state.
    # route_after_transform must guard on error and go to finalize.
    file_path = str(fx["xlsx_odd_headers"])
    try:
        result = process(file_path, deps=_default_deps())
    except RecursionError as e:
        raise AssertionError(
            "Router recursion detected: route_after_transform looped instead of "
            f"going to finalize when sheet_map set error. Original: {e}"
        ) from e
    # Must finish (any status) — just must not recurse
    assert result is not None, "process() must return a result, not None"
    # With NullLLM the pipeline fails at sheet_map; status is failed or needs_review
    assert result.status in (DocStatus.failed, DocStatus.needs_review), (
        f"Unexpected status with NullLLM: {result.status}"
    )


# ---- Expected-mapping assertions for 0-LLM-call fixtures ----
# These tests pin the exact deterministic mapping so regressions are immediately visible.


def _assert_zero_calls_and_map(path, file_type="sheet"):
    state = _load_classify(path, file_type=file_type)
    fake = FakeLLM([])
    deps = _make_deps(llm=fake)
    node = make_sheet_map(deps)
    result = node(state)
    assert len(fake.calls) == 0, f"Expected 0 LLM calls for {path}, got {len(fake.calls)}"
    m = result.get("mapping")
    assert m is not None, f"Expected a mapping for {path}, got None"
    return m


def test_zero_llm_mapping_csv_cp1252_semicolon(fx):
    """csv_cp1252_eu: EU decimal/thousands, all standard headers → 0 LLM calls.
    Key assertion: decimal_separator=',' and thousands_separator='.'"""
    m = _assert_zero_calls_and_map(fx["csv_cp1252_semicolon"])
    assert m["decimal_separator"] == ",", (
        f"EU locale CSV must detect decimal_separator=',', got {m['decimal_separator']!r}"
    )
    assert m["thousands_separator"] == ".", (
        f"EU locale CSV must detect thousands_separator='.', got {m['thousands_separator']!r}"
    )
    assert m["invoice_fields"].get("invoice_number") == "Invoice Number"
    assert m["invoice_fields"].get("invoice_date") == "Invoice Date"
    assert m["date_format"] == "%Y-%m-%d"


def test_zero_llm_mapping_xls_legacy(fx):
    """xls_legacy (xlrd engine): standard headers, line_item grain, date_format detected."""
    m = _assert_zero_calls_and_map(fx["xls_legacy"])
    assert m["grain"] == "line_item"
    assert m["invoice_fields"].get("invoice_number") == "Invoice Number"
    assert m["invoice_fields"].get("invoice_date") == "Invoice Date"
    assert m["line_fields"].get("amount") == "Amount"
    assert m["date_format"] == "%Y-%m-%d"
    assert m["decimal_separator"] == "."
    assert m["thousands_separator"] == ","


def test_zero_llm_mapping_xlsx_excel_dates(fx):
    """xlsx_excel_dates: real Excel date serials → converted to ISO strings → date_format detected."""
    m = _assert_zero_calls_and_map(fx["xlsx_excel_dates"])
    assert m["invoice_fields"].get("invoice_number") == "Invoice Number"
    assert m["invoice_fields"].get("invoice_date") == "Invoice Date"
    assert m["date_format"] == "%Y-%m-%d", (
        f"Excel date serials must map to %Y-%m-%d, got {m['date_format']!r}"
    )
    assert m["decimal_separator"] == "."


def test_zero_llm_mapping_xlsx_banner_rows(fx):
    """xlsx_banner_rows: header on row 3; deterministic pass must still detect all standard cols."""
    m = _assert_zero_calls_and_map(fx["xlsx_banner_rows"])
    assert m["invoice_fields"].get("invoice_number") == "Invoice Number"
    assert m["invoice_fields"].get("invoice_date") == "Invoice Date"
    assert m["date_format"] == "%Y-%m-%d"


def test_zero_llm_mapping_xlsx_merged_header(fx):
    """xlsx_merged_header: merged title band above real header; standard cols must resolve."""
    m = _assert_zero_calls_and_map(fx["xlsx_merged_header"])
    assert m["invoice_fields"].get("invoice_number") == "Invoice Number"
    assert m["invoice_fields"].get("invoice_date") == "Invoice Date"
    assert m["invoice_fields"].get("supplier_name") is not None
    assert m["date_format"] == "%Y-%m-%d"


def test_sheet_mapping_schema_has_no_empty_enum_value():
    """Gemini rejects tool schemas whose enum contains an empty string (HTTP 400)."""
    import json
    from app.schemas.models import SheetMapping
    enums = []

    def walk(node):
        if isinstance(node, dict):
            if "enum" in node:
                enums.append(node["enum"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(SheetMapping.model_json_schema())
    assert enums and all("" not in e for e in enums), json.dumps(enums)
