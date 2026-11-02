"""segment node — split page text into per-invoice units.

Fix 2: reads low_conf_pages and marks unit["needs_vision"]=True when any of its pages
  are low-confidence. In the stub, unit id == page id (no real page-range assignment yet).

Stub: real segmentation logic implemented in M6.
"""
from __future__ import annotations

from app.pipeline.state import State, node


@node("segment")
def segment(s: State) -> dict:
    sim = s.get("sim", {})
    n = sim.get("units", 1)
    low_pages = set(s.get("low_conf_pages", []))
    # stub: unit id == page id; segment maps pages to units in real impl
    units = [{"id": i, "needs_vision": i in low_pages} for i in range(n)]
    return {"units": units}
