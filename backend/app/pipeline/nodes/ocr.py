"""ocr node — run RapidOCR on scanned pages, record confidence.

Fix 2: writes low_conf_pages (list of page ints), NOT vision_queue.
  In the stub, sim.low_conf_units is treated as page ids (unit id == page id in stubs).
  Real mapping (page -> unit) is done in segment. Explicit comment kept as reminder.

Stub: real OCR logic implemented in M5.
"""
from __future__ import annotations

from app.pipeline.state import State, node


@node("ocr")
def ocr(s: State) -> dict:
    sim = s.get("sim", {})
    # stub: treat sim.low_conf_units as low-confidence page ids
    # (in the real impl, ocr works on pages; segment maps pages -> units)
    low_pages: list[int] = list(sim.get("low_conf_units", []))
    conf = {p: 0.5 if p in low_pages else 0.95 for p in s.get("scanned_pages", [])}
    return {
        "ocr_conf": conf,
        "low_conf_pages": low_pages,  # Fix 2: page ids, read later by segment
    }
