"""Pipeline graph state, node wrapper, and Deps container.

Fixes applied:
  Fix 7  — error_code (ErrorCode enum) + error (str) in state, both set on failure
  Fix 9  — additional state keys: page_source, page_images, low_conf_pages,
            mapping_feedback, warnings, llm_usage, frames, error_code
  Fix 10 — history entries include attempt (int) and meta (dict)
"""
from __future__ import annotations

import time
from functools import wraps
from typing import TYPE_CHECKING, Annotated, Any, Optional
import operator

from app.schemas.models import ErrorCode

if TYPE_CHECKING:
    from app.pipeline.config import Config
    from app.pipeline.llm.client import LLMClient

try:
    from typing import TypedDict
except ImportError:
    from typing_extensions import TypedDict


SHEET_EXT = {".xlsx", ".xls", ".csv"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


# ---------------------------------------------------------------------- Deps

class Deps(TypedDict, total=False):
    config: "Config"
    llm: "LLMClient"
    seen_store: Any     # callable(file_hash) -> bool  (duplicate check)
    mapping_cache: Any  # callable(header_hash) -> SheetMapping | None
    image_temp_dir: str  # M5: temp dir for page PNGs, created by process(); cleaned on exit


# ---------------------------------------------------------------------- State

class State(TypedDict, total=False):
    # --- identity
    file_path: str
    file_type: str          # pdf | image | sheet
    file_hash: str
    size_bytes: int
    page_count: int

    # --- acquisition: PDF
    digital_pages: list[int]
    scanned_pages: list[int]
    page_labels: dict[int, str]       # M3: per-page triage label: digital | scanned | garbled
    page_text: dict[int, str]
    page_source: dict[int, str]     # Fix 9: digital|ocr|vision|sheet per page
    page_images: dict[int, str]     # M5: {page_idx: png_path} — never bytes
    image_quality: dict[int, dict]  # M5: {page_idx: {blur, side_px, contrast}}
    tables: dict[int, list]         # page -> list of table markdown strings

    # --- acquisition: OCR
    ocr_conf: dict[int, float]
    low_conf_pages: list[int]       # Fix 9 + Fix 2: written by ocr, read by segment

    # --- acquisition: sheet
    sheets: list[str]               # sheet names
    frames: Any                     # in-memory DataFrames; stripped by finalize
    sheet_kind: str                 # tabular | freeform
    primary_sheet: str
    mapping: dict
    mapping_feedback: str           # Fix 9: validation error text fed back to sheet_map
    mapping_retries: int
    parse_ok: bool

    # --- extraction
    units: list[dict]               # invoice units after segmentation
    vision_queue: list[int]         # Fix 2: unit ids only (never page ids)
    escalated: bool                 # vision escalation already used

    # --- results
    invoices: list[dict]
    validation_errors: list[dict]
    warnings: list[dict]            # Fix 9: document-level warning issues
    llm_usage: dict                 # Fix 9: {calls, input_tokens, output_tokens}

    # --- final status
    status: str                     # validated_ok | needs_review | failed
    error_code: Optional[str]       # Fix 7: ErrorCode value (str enum)
    error: Optional[str]            # Fix 7: human-readable message
    result: dict                    # serialised PipelineResult built by finalize/fail

    # --- bookkeeping
    history: Annotated[list[dict], operator.add]
    sim: dict                       # dry-run switches; removed at end of M7


# ----------------------------------------------------------------- @node wrapper

def node(name: str, skip_on_error: bool = True):
    """Times the node, records history (with attempt + meta, Fix 10),
    converts unexpected exceptions into state error."""

    def deco(fn):
        @wraps(fn)
        def run(state: State) -> dict:
            if skip_on_error and state.get("error"):
                return {}
            t0 = time.perf_counter()
            ok = True
            attempt = state.get("sim", {}).get(f"_attempt_{name}", 0)
            meta: dict = {}
            try:
                if state.get("sim", {}).get("raise_in") == name:
                    raise RuntimeError("injected failure")
                out = fn(state) or {}
                meta = out.pop("_meta", {})
            except Exception as exc:  # noqa: BLE001
                if getattr(exc, "_propagate", False):
                    raise
                out = {
                    "error": f"{name}: {exc}",
                    "error_code": ErrorCode.INTERNAL_ERROR.value,
                    "status": "failed",
                }
                ok = False
            # Fix 10: history entry includes attempt and meta
            out["history"] = [{
                "node": name,
                "seconds": round(time.perf_counter() - t0, 5),
                "ok": ok,
                "attempt": attempt,
                "meta": meta,
            }]
            return out

        return run

    return deco
