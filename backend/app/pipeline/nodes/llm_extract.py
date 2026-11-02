"""llm_extract node — per-unit structured extraction via LLM.

Fix 2: units with needs_vision=True are skipped here; their ids go to vision_queue.
Fix 3: sim.extract_fail_units simulates retry exhaustion -> EXTRACTION_FAILED issue,
  unit escalated to vision_queue, no exception raised.

Stub: real LLM calls implemented in M6.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.pipeline.state import State, node
from app.schemas.models import IssueCode, Severity


_STUB_INVOICE_DATE = date(2026, 1, 1)
_STUB_DUE_DATE = date(2026, 2, 1)
_STUB_SUBTOTAL = Decimal("100.00")
_STUB_TAX = Decimal("10.00")
_STUB_TOTAL = Decimal("110.00")
_STUB_ARITH_TOTAL = Decimal("999.00")


def _make_invoice(uid: int, total: Decimal) -> dict:
    inv_num = f"INV-{uid + 1:03d}"
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
        total = _STUB_ARITH_TOTAL if wrong_total else _STUB_TOTAL
        invoices.append(_make_invoice(uid, total))

    return {"invoices": invoices, "vision_queue": sorted(queue)}
