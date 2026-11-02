"""Document Intelligence pipeline: LangGraph skeleton.

Nodes are stubs driven by state["sim"] switches so the whole topology can be
compiled and exercised end to end. Replace stub bodies with real logic one by
one; the wiring, state and routers stay as they are.

Contract: the graph is a pure function  process(file_path) -> final state.
No DB, no HTTP inside. The caller persists the result.
"""
from __future__ import annotations

import operator
import time
from functools import wraps
from pathlib import Path
from typing import Annotated, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

SHEET_EXT = {".xlsx", ".xls", ".csv"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


# --------------------------------------------------------------------- state
class State(TypedDict, total=False):
    file_path: str
    file_type: str                      # pdf | image | sheet
    file_hash: str
    page_count: int
    # acquisition
    digital_pages: list[int]
    scanned_pages: list[int]
    page_text: dict[int, str]
    ocr_conf: dict[int, float]
    sheet_kind: str                     # tabular | freeform
    mapping: dict
    mapping_retries: int
    parse_ok: bool
    # extraction
    units: list[dict]                   # invoice units after segmentation
    vision_queue: list[int]             # unit ids that need tier 3
    escalated: bool                     # vision escalation already used
    invoices: list[dict]
    validation_errors: list[dict]
    # result
    status: str                         # validated_ok | needs_review | failed
    error: Optional[str]
    history: Annotated[list[dict], operator.add]
    sim: dict                           # dry-run switches; remove with the stubs


# ------------------------------------------------------------------- wrapper
def node(name: str, skip_on_error: bool = True):
    """Times the node, records history, converts exceptions into state['error'].
    After an error every downstream node (except finalize/fail) no-ops, so the
    flow drains to the end instead of crashing on missing state."""

    def deco(fn):
        @wraps(fn)
        def run(state: State) -> dict:
            if skip_on_error and state.get("error"):
                return {}
            t0 = time.perf_counter()
            ok = True
            try:
                if state.get("sim", {}).get("raise_in") == name:  # dry-run fault injection
                    raise RuntimeError("injected failure")
                out = fn(state) or {}
            except Exception as e:  # noqa: BLE001
                out, ok = {"error": f"{name}: {e}"}, False
            out["history"] = [{"node": name, "seconds": round(time.perf_counter() - t0, 5), "ok": ok}]
            return out

        return run

    return deco


# --------------------------------------------------------------------- nodes
@node("intake")
def intake(s: State) -> dict:
    # real: magic bytes, size limit, sha256, open test, encrypted check
    sim, ext = s.get("sim", {}), Path(s["file_path"]).suffix.lower()
    if sim.get("corrupt"):
        return {"error": "intake: corrupt file", "status": "failed"}
    if ext == ".pdf":
        return {"file_type": "pdf", "page_count": sim.get("pages", 3)}
    if ext in IMAGE_EXT:
        return {"file_type": "image", "page_count": 1}
    if ext in SHEET_EXT:
        return {"file_type": "sheet"}
    return {"error": f"intake: unsupported type {ext}", "status": "failed"}


@node("pdf_triage")
def pdf_triage(s: State) -> dict:
    # real: chars per page, image coverage, garbled-text detection
    scanned = s.get("sim", {}).get("scanned_pages", [])
    digital = [p for p in range(s["page_count"]) if p not in scanned]
    return {"digital_pages": digital, "scanned_pages": scanned}


@node("pdf_text")
def pdf_text(s: State) -> dict:
    # real: PyMuPDF text + find_tables (pdfplumber fallback)
    return {"page_text": {p: f"<text p{p}>" for p in s["digital_pages"]}}


@node("image_prep")
def image_prep(s: State) -> dict:
    # real: render 200-300 DPI, split TIFF frames, deskew, quality score
    if s["file_type"] == "image":
        return {"scanned_pages": [0]}
    return {}


@node("ocr")
def ocr(s: State) -> dict:
    # real: RapidOCR (Tesseract fallback); low-confidence pages go to vision
    low = s.get("sim", {}).get("low_conf_units", [])
    return {"ocr_conf": {p: 0.5 if p in low else 0.95 for p in s["scanned_pages"]},
            "vision_queue": list(low)}


@node("sheet_load")
def sheet_load(s: State) -> dict:
    # real: pandas dtype=str, header detection, encoding sniffing, .xls via xlrd
    return {}


@node("sheet_classify")
def sheet_classify(s: State) -> dict:
    return {"sheet_kind": "freeform" if s.get("sim", {}).get("freeform") else "tabular"}


@node("sheet_map")
def sheet_map(s: State) -> dict:
    # real: ONE LLM call on column names + sample rows -> validated mapping JSON
    return {"mapping": {"grain": "line_item", "group_by": "Inv No"}}


@node("sheet_transform")
def sheet_transform(s: State) -> dict:
    # real: pandas transform + parse-rate check (>=95%) + groupby into invoices
    attempt = s.get("mapping_retries", 0)
    ok = attempt >= s.get("sim", {}).get("bad_mapping", 0)
    if ok:
        return {"parse_ok": True, "invoices": [{"unit": 0, "tier": "sheet"}]}
    return {"parse_ok": False, "mapping_retries": attempt + 1}


@node("segment")
def segment(s: State) -> dict:
    # real: split long docs into invoice units (anchors, "page x of y")
    n = s.get("sim", {}).get("units", 1)
    return {"units": [{"id": i} for i in range(n)]}


@node("llm_extract")
def llm_extract(s: State) -> dict:
    # real: per-unit structured output, Pydantic, retry with error feedback
    queue = set(s.get("vision_queue", []))
    queue |= set(s.get("sim", {}).get("extract_fail_units", []))
    invoices = [{"unit": u["id"], "tier": "text"} for u in s["units"] if u["id"] not in queue]
    return {"invoices": invoices, "vision_queue": sorted(queue)}


@node("validate")
def validate(s: State) -> dict:
    # real: completeness, Decimal arithmetic, grounding, dates, duplicates
    sim = s.get("sim", {})
    errors = []
    bad = set(sim.get("arith_fail_units", []))
    for inv in s.get("invoices", []):
        persist = inv["unit"] in set(sim.get("persistent_fail_units", []))
        if persist or (inv["unit"] in bad and inv["tier"] != "vision"):
            errors.append({"unit": inv["unit"], "code": "ARITH_MISMATCH", "severity": "error"})
    out = {"validation_errors": errors}
    if not s.get("escalated"):
        out["vision_queue"] = sorted(set(s.get("vision_queue", [])) | {e["unit"] for e in errors})
    return out


@node("vision_extract")
def vision_extract(s: State) -> dict:
    # real: page images -> vision model, same schema + source_text transcription
    queue = set(s["vision_queue"])
    kept = [i for i in s.get("invoices", []) if i["unit"] not in queue]
    redone = [{"unit": u, "tier": "vision"} for u in sorted(queue)]
    return {"invoices": kept + redone, "escalated": True, "vision_queue": []}


@node("finalize", skip_on_error=False)
def finalize(s: State) -> dict:
    if s.get("error"):
        status = "failed"
    elif not s.get("parse_ok", True):
        status = "needs_review"
    elif any(e["severity"] == "error" for e in s.get("validation_errors", [])):
        status = "needs_review"
    else:
        status = "validated_ok"
    return {"status": status}


@node("fail", skip_on_error=False)
def fail(s: State) -> dict:
    return {"status": "failed"}


NODES = {
    "intake": intake, "pdf_triage": pdf_triage, "pdf_text": pdf_text,
    "image_prep": image_prep, "ocr": ocr, "sheet_load": sheet_load,
    "sheet_classify": sheet_classify, "sheet_map": sheet_map,
    "sheet_transform": sheet_transform, "segment": segment,
    "llm_extract": llm_extract, "validate": validate,
    "vision_extract": vision_extract, "finalize": finalize, "fail": fail,
}


# ------------------------------------------------------------------- routers
def route_after_intake(s: State) -> str:
    if s.get("error"):
        return "fail"
    return {"pdf": "pdf_triage", "image": "image_prep", "sheet": "sheet_load"}[s["file_type"]]


def route_after_pdf_text(s: State) -> str:
    return "image_prep" if s.get("scanned_pages") else "segment"


def route_after_classify(s: State) -> str:
    return "sheet_map" if s["sheet_kind"] == "tabular" else "segment"


def route_after_transform(s: State) -> str:
    if s.get("parse_ok"):
        return "validate"
    return "sheet_map" if s.get("mapping_retries", 0) < 2 else "finalize"  # one retry


def route_after_validate(s: State) -> str:
    if s.get("vision_queue") and not s.get("escalated"):
        return "vision_extract"
    return "finalize"


# --------------------------------------------------------------------- graph
def build_graph():
    g = StateGraph(State)
    for name, fn in NODES.items():
        g.add_node(name, fn)

    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", route_after_intake,
                            ["pdf_triage", "image_prep", "sheet_load", "fail"])
    # PDF / image acquisition
    g.add_edge("pdf_triage", "pdf_text")
    g.add_conditional_edges("pdf_text", route_after_pdf_text, ["image_prep", "segment"])
    g.add_edge("image_prep", "ocr")
    g.add_edge("ocr", "segment")
    # spreadsheet acquisition
    g.add_edge("sheet_load", "sheet_classify")
    g.add_conditional_edges("sheet_classify", route_after_classify, ["sheet_map", "segment"])
    g.add_edge("sheet_map", "sheet_transform")
    g.add_conditional_edges("sheet_transform", route_after_transform,
                            ["validate", "sheet_map", "finalize"])
    # extraction, validation, escalation
    g.add_edge("segment", "llm_extract")
    g.add_edge("llm_extract", "validate")
    g.add_conditional_edges("validate", route_after_validate, ["vision_extract", "finalize"])
    g.add_edge("vision_extract", "validate")
    g.add_edge("finalize", END)
    g.add_edge("fail", END)
    return g.compile()


def process(file_path: str, sim: dict | None = None) -> State:
    return build_graph().invoke({"file_path": file_path, "sim": sim or {}, "history": []})
