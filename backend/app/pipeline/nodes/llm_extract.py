"""llm_extract node — per-unit structured extraction via LLM.

Fix 2: units with needs_vision=True are skipped here; their ids go to vision_queue.
Fix 3: sim.extract_fail_units simulates retry exhaustion -> EXTRACTION_FAILED issue,
  unit escalated to vision_queue, no exception raised.
M3 addition: when sim["gt_invoices"] is present, stub uses GT printed values as raw
  so grounding validation passes with real page text. Removed in M7.

Stub: real LLM calls implemented in M6.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.pipeline.state import State, node
from app.pipeline.utils.money import parse_amount
from app.schemas.models import IssueCode, Severity


_STUB_INVOICE_DATE = date(2026, 1, 1)
_STUB_DUE_DATE = date(2026, 2, 1)
_STUB_SUBTOTAL = Decimal("100.00")
_STUB_TAX = Decimal("10.00")
_STUB_TOTAL = Decimal("110.00")
_STUB_ARITH_TOTAL = Decimal("999.00")


def _gt_invoice(s: State, uid: int) -> dict | None:
    """Look up GT invoice by filename and unit index via sim["gt_invoices"]."""
    gt_map = s.get("sim", {}).get("gt_invoices", {})
    filename = Path(s.get("file_path", "")).name
    invs = gt_map.get(filename, [])
    return invs[uid] if uid < len(invs) else None


def _make_invoice(uid: int, arith_fail: bool, s: State) -> dict:
    gt = _gt_invoice(s, uid)
    if gt and "printed" in gt:
        p = gt["printed"]
        sub_str = p.get("subtotal") or str(_STUB_SUBTOTAL)
        tax_str = p.get("tax") or str(_STUB_TAX)
        tot_val = _STUB_ARITH_TOTAL if arith_fail else (parse_amount(p.get("total") or "") or _STUB_TOTAL)
        inv_num = p.get("invoice_number") or f"INV-{uid + 1:03d}"
        return {
            "unit_id": uid,
            "tier": "parser",
            "invoice_number": inv_num,
            "invoice_date": _STUB_INVOICE_DATE,
            "due_date": _STUB_DUE_DATE,
            "supplier_name": p.get("supplier_name") or "Acme Corp",
            "customer_name": p.get("customer_name") or "Customer Ltd",
            "subtotal": parse_amount(sub_str),
            "tax": parse_amount(tax_str),
            "total": tot_val,
            "currency": p.get("currency") or "USD",
            "line_items": [{"position": 1, "description": "Service", "amount": parse_amount(sub_str)}],
            "source_pages": [uid],
            "raw": {
                "invoice_number": inv_num,
                "invoice_date": p.get("invoice_date") or "2026-01-01",
                "subtotal": sub_str,
                "tax": tax_str,
                "total": str(tot_val),
            },
        }

    # Fallback: original stub values
    inv_num = f"INV-{uid + 1:03d}"
    total = _STUB_ARITH_TOTAL if arith_fail else _STUB_TOTAL
    return {
        "unit_id": uid,
        "tier": "parser",
        "invoice_number": inv_num,
        "invoice_date": _STUB_INVOICE_DATE,
        "due_date": _STUB_DUE_DATE,
        "supplier_name": "Acme Corp",
        "customer_name": "Customer Ltd",
        "subtotal": _STUB_SUBTOTAL,
        "tax": _STUB_TAX,
        "total": total,
        "currency": "USD",
        "line_items": [{"position": 1, "description": "Service", "amount": _STUB_SUBTOTAL}],
        "source_pages": [uid],
        "raw": {
            "invoice_number": inv_num,
            "invoice_date": "2026-01-01",
            "due_date": "2026-02-01",
            "subtotal": str(_STUB_SUBTOTAL),
            "tax": str(_STUB_TAX),
            "total": str(total),
        },
    }


@node("llm_extract")
def llm_extract(s: State) -> dict:
    sim = s.get("sim", {})
    fail_units = set(sim.get("extract_fail_units", []))
    arith_fail = set(sim.get("arith_fail_units", []))
    persistent_fail = set(sim.get("persistent_fail_units", []))
    queue = set(s.get("vision_queue", []))

    invoices = []
    issues_by_unit: dict[int, list] = {}

    for u in s.get("units", []):
        uid = u["id"]
        if u.get("needs_vision") or uid in queue:
            # Fix 2: already flagged low-confidence; goes straight to vision
            queue.add(uid)
            continue
        if uid in fail_units:
            # Fix 3: extraction failed after retries; escalate, do not raise
            queue.add(uid)
            issues_by_unit[uid] = [{
                "code": IssueCode.EXTRACTION_FAILED.value,
                "severity": Severity.error.value,
                "message": f"unit {uid}: extraction exhausted retries",
                "field": None, "expected": None, "got": None, "location": {},
            }]
            continue

        wrong_total = uid in arith_fail or uid in persistent_fail
        invoices.append(_make_invoice(uid, wrong_total, s))

    return {"invoices": invoices, "vision_queue": sorted(queue)}
