"""vision_extract node — re-extract queued units via vision LLM.

Fix 3: sim.vision_fail_units -> VISION_FAILED issue added, text-tier invoice kept.
Fix 4: arbitration after vision — keep candidate with fewer error-severity issues;
  tie goes to vision. Losing candidate stored in invoice["alternatives"].

Stub: real vision LLM calls implemented in M6.
"""
from __future__ import annotations

from app.pipeline.state import State, node
from app.schemas.models import IssueCode, Severity


def _error_count(inv: dict, errors: list[dict]) -> int:
    unit = inv.get("unit")
    return sum(
        1 for e in errors
        if e.get("unit") == unit and e.get("severity") == Severity.error.value
    )


@node("vision_extract")
def vision_extract(s: State) -> dict:
    sim = s.get("sim", {})
    queue = set(s.get("vision_queue", []))
    fail_units = set(sim.get("vision_fail_units", []))
    val_errors = s.get("validation_errors", [])

    kept = [i for i in s.get("invoices", []) if i.get("unit") not in queue]
    new_invoices: list[dict] = []
    extra_errors: list[dict] = []

    for uid in sorted(queue):
        if uid in fail_units:
            # Fix 3: vision failed; keep text-tier if present, add issue
            text_inv = next((i for i in s.get("invoices", []) if i.get("unit") == uid), None)
            extra_errors.append({
                "unit": uid, "tier": "vision",
                "code": IssueCode.VISION_FAILED.value,
                "severity": Severity.error.value,
                "message": f"unit {uid}: vision extraction failed",
                "field": None, "expected": None, "got": None, "location": {},
            })
            if text_inv:
                new_invoices.append(text_inv)
            continue

        vision_inv = {"unit": uid, "tier": "vision", "alternatives": []}

        # Fix 4: arbitration — compare error counts
        text_inv = next((i for i in s.get("invoices", []) if i.get("unit") == uid), None)
        if text_inv is not None:
            vision_errors = _error_count(vision_inv, val_errors)
            text_errors = _error_count(text_inv, val_errors)
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
                {"tier": "text", "reason": "arbitration: vision selected (tie or fewer errors)"}
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
