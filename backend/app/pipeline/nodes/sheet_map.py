"""sheet_map node — one LLM call to map column names to invoice fields.

Stub: real LLM call implemented in M4.
"""
from __future__ import annotations

from app.pipeline.state import State, node


@node("sheet_map")
def sheet_map(s: State) -> dict:
    return {"mapping": {"grain": "line_item", "group_by": "Inv No"}}
