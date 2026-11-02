"""image_prep node — render pages to images, quality-score them.

Stub: real logic (DPI render, deskew, blur check) implemented in M5.
"""
from __future__ import annotations

from app.pipeline.state import State, node


@node("image_prep")
def image_prep(s: State) -> dict:
    if s["file_type"] == "image":
        return {"scanned_pages": [0]}
    return {}
