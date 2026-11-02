"""validate node — arithmetic, completeness, grounding, date checks.

Fix 1: vision escalation is never applied to sheet invoices.
  Only invoices where tier in {"text", "ocr"} AND file_type != "sheet"
  may be added to vision_queue.

Stub: real validation rules implemented in M1.
"""
from __future__ import annotations

from app.pipeline.state import State, node
from app.schemas.models import IssueCode, Severity


@node("validate")
def validate(s: State) -> dict:
    sim = s.get("sim", {})
    file_type = s.get("file_type", "")
    errors: list[dict] = []
    bad = set(sim.get("arith_fail_units", []))
    persistent = set(sim.get("persistent_fail_units", []))

    for inv in s.get("invoices", []):
        unit = inv.get("unit", 0)
        tier = inv.get("tier", "text")

        # mark arithmetic failures from sheet_transform passthrough
        if inv.get("_arith_fail"):
            errors.append({
                "unit": unit, "tier": tier,
                "code": IssueCode.ARITH_TOTAL.value,
                "severity": Severity.error.value,
                "message": "arithmetic check failed",
                "field": "total", "expected": None, "got": None, "location": {},
            })
            continue

        # persistent units fail even after vision re-extraction (no escalation helps)
        if unit in persistent or (unit in bad and tier != "vision"):
            errors.append({
                "unit": unit, "tier": tier,
                "code": IssueCode.ARITH_TOTAL.value,
                "severity": Severity.error.value,
                "message": "arithmetic check failed",
                "field": "total", "expected": None, "got": None, "location": {},
            })

    out: dict = {"validation_errors": errors}

    # Fix 1: only non-sheet, non-vision-already-escalated invoices go to vision_queue
    if not s.get("escalated"):
        can_escalate = {
            e["unit"] for e in errors
            if e.get("tier") in {"parser", "ocr"} and file_type != "sheet"
        }
        existing_queue = set(s.get("vision_queue", []))
        out["vision_queue"] = sorted(existing_queue | can_escalate)

    return out
