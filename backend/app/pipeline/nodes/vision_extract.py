"""vision_extract node — re-extract queued units via vision LLM.

Fix 3: sim.vision_fail_units -> VISION_FAILED issue added, text-tier invoice kept.
Fix 4: arbitration after vision — keep candidate with fewer error-severity issues;
  tie goes to vision. Losing candidate stored in invoice["alternatives"].

Stub: real vision LLM calls implemented in M6.
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


def _make_vision_invoice(uid: int, total: Decimal) -> dict:
    inv_num = f"INV-{uid + 1:03d}"
    return {
        "unit_id": uid,
        "tier": "vision",
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
        "alternatives": [],
    }


def _error_count(unit_id: int | None, errors: list[dict]) -> int:
    return sum(
        1 for e in errors
        if e.get("unit_id") == unit_id and e.get("severity") == Severity.error.value
    )


@node("vision_extract")
def vision_extract(s: State) -> dict:
    sim = s.get("sim", {})
    queue = set(s.get("vision_queue", []))
    fail_units = set(sim.get("vision_fail_units", []))
    persistent_fail = set(sim.get("persistent_fail_units", []))
    arith_fail = set(sim.get("arith_fail_units", []))
    val_errors = s.get("validation_errors", [])

    kept = [i for i in s.get("invoices", []) if i.get("unit_id") not in queue]
    new_invoices: list[dict] = []
    extra_errors: list[dict] = []

    for uid in sorted(queue):
        if uid in fail_units:
            # Fix 3: vision failed; keep text-tier if present, add issue
            text_inv = next(
                (i for i in s.get("invoices", []) if i.get("unit_id") == uid), None
            )
            extra_errors.append({
                "unit_id": uid, "tier": "vision",
                "code": IssueCode.VISION_FAILED.value,
                "severity": Severity.error.value,
                "message": f"unit {uid}: vision extraction failed",
                "field": None, "expected": None, "got": None, "location": {},
            })
            if text_inv:
                new_invoices.append(text_inv)
            continue

        # Persistent fail keeps wrong total; arith_fail that isn't persistent gets correct total
        wrong_total = uid in persistent_fail
        total = _STUB_ARITH_TOTAL if wrong_total else _STUB_TOTAL
        vision_inv = _make_vision_invoice(uid, total)

        # Fix 4: arbitration — compare error counts
        text_inv = next(
            (i for i in s.get("invoices", []) if i.get("unit_id") == uid), None
        )
        if text_inv is not None:
            vision_errors = _error_count(uid, val_errors)
            text_errors = _error_count(uid, val_errors)
            if text_errors < vision_errors:
                # text wins; store vision as alternative
                winner = dict(text_inv)
                winner.setdefault("alternatives", []).append(
                    {"tier": "vision", "reason": "arbitration: text had fewer errors"}
                )
                new_invoices.append(winner)
                continue
            # vision wins (tie or fewer errors); store text as alternative
            vision_inv["alternatives"].append(
                {"tier": "parser", "reason": "arbitration: vision selected (tie or fewer errors)"}
            )

        new_invoices.append(vision_inv)

    all_invoices = kept + new_invoices
    all_errors = val_errors + extra_errors

    return {
        "invoices": all_invoices,
        "validation_errors": all_errors,
        "escalated": True,
        "vision_queue": [],
    }
