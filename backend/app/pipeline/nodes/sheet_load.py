"""sheet_load node — load spreadsheet with pandas, detect header row.

Stub: real logic (dtype=str, encoding sniff, xlrd for .xls) implemented in M4.
"""
from __future__ import annotations

from app.pipeline.state import State, node


@node("sheet_load")
def sheet_load(s: State) -> dict:
    return {}
