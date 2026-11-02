"""sheet_transform node — apply mapping via pandas, group into invoices.

Stub: real pandas transform implemented in M4.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.pipeline.state import State, node


_STUB_INVOICE_DATE = date(2026, 1, 1)
_STUB_DUE_DATE = date(2026, 2, 1)
_STUB_SUBTOTAL = Decimal("100.00")
_STUB_TAX = Decimal("10.00")
_STUB_TOTAL = Decimal("110.00")
_STUB_ARITH_TOTAL = Decimal("999.00")


@node("sheet_transform")
def sheet_transform(s: State) -> dict:
    sim = s.get("sim", {})
    attempt = s.get("mapping_retries", 0)
    ok = attempt >= sim.get("bad_mapping", 0)
    if ok:
        arith_fail = set(sim.get("arith_fail_units", []))
        total = _STUB_ARITH_TOTAL if arith_fail else _STUB_TOTAL
        inv = {
            "unit_id": None,
            "tier": "sheet",
            "invoice_number": "INV-001",
            "invoice_date": _STUB_INVOICE_DATE,
            "due_date": _STUB_DUE_DATE,
            "supplier_name": "Acme Corp",
            "customer_name": "Customer Ltd",
            "subtotal": _STUB_SUBTOTAL,
            "tax": _STUB_TAX,
            "total": total,
            "currency": "USD",
            "line_items": [{"position": 1, "description": "Service", "amount": _STUB_SUBTOTAL}],
            "source_rows": [],
            "raw": {
                "invoice_number": "INV-001",
                "invoice_date": "2026-01-01",
                "due_date": "2026-02-01",
                "subtotal": str(_STUB_SUBTOTAL),
                "tax": str(_STUB_TAX),
                "total": str(total),
            },
        }
        return {"parse_ok": True, "invoices": [inv]}
    return {"parse_ok": False, "mapping_retries": attempt + 1}
