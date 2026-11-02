"""sheet_transform node — apply mapping via pandas, group into invoices.

Stub: real pandas transform implemented in M4.
"""
from __future__ import annotations

from app.pipeline.state import State, node


@node("sheet_transform")
def sheet_transform(s: State) -> dict:
    sim = s.get("sim", {})
    attempt = s.get("mapping_retries", 0)
    ok = attempt >= sim.get("bad_mapping", 0)
    if ok:
        arith_fail = set(sim.get("arith_fail_units", []))
        # sheets produce unit_id=None per docs/02; use index 0 for the stub
        inv = {"unit": 0, "tier": "sheet"}
        if arith_fail:
            inv["_arith_fail"] = True
        return {"parse_ok": True, "invoices": [inv]}
    return {"parse_ok": False, "mapping_retries": attempt + 1}
