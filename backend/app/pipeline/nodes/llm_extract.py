"""llm_extract node — per-unit structured extraction via LLM.

Fix 2: units with needs_vision=True are skipped here; their ids go to vision_queue.
Fix 3: sim.extract_fail_units simulates retry exhaustion -> EXTRACTION_FAILED issue,
  unit escalated to vision_queue, no exception raised.

Stub: real LLM calls implemented in M6.
"""
from __future__ import annotations

from app.pipeline.state import State, node
from app.schemas.models import IssueCode, Severity


@node("llm_extract")
def llm_extract(s: State) -> dict:
    sim = s.get("sim", {})
    fail_units = set(sim.get("extract_fail_units", []))
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
        invoices.append({"unit": uid, "tier": "parser"})

    return {"invoices": invoices, "vision_queue": sorted(queue)}
