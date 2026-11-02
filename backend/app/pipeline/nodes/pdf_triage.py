"""pdf_triage node — label pages as digital or scanned.

Stub: real logic (chars/page, image coverage, garbled text) implemented in M3.
"""
from __future__ import annotations

from app.pipeline.state import State, node


@node("pdf_triage")
def pdf_triage(s: State) -> dict:
    scanned = s.get("sim", {}).get("scanned_pages", [])
    digital = [p for p in range(s["page_count"]) if p not in scanned]
    return {"digital_pages": digital, "scanned_pages": scanned}
