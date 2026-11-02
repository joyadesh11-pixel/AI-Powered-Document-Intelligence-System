"""sheet_classify node — decide tabular vs free-form layout.

Stub: real logic implemented in M4.
"""
from __future__ import annotations

from app.pipeline.state import State, node


@node("sheet_classify")
def sheet_classify(s: State) -> dict:
    kind = "freeform" if s.get("sim", {}).get("freeform") else "tabular"
    return {"sheet_kind": kind}
