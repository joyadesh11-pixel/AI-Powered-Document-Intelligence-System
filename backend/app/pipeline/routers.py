"""Conditional edge routers for the pipeline graph.

Fix 1:  route_after_validate — sheet invoices never go to vision_extract.
        The validate node already prevents them entering vision_queue, but an
        explicit file_type guard here makes the invariant visible at the routing layer.
Fix 11: route_after_transform — uses config.MAPPING_MAX_RETRIES (=1) explicitly
        instead of a magic literal, so the intent ("one retry allowed") is clear.
"""
from __future__ import annotations

from app.pipeline.state import State
from app.pipeline.config import Config


def route_after_intake(s: State) -> str:
    if s.get("error"):
        return "fail"
    return {"pdf": "pdf_triage", "image": "image_prep", "sheet": "sheet_load"}[s["file_type"]]


def route_after_pdf_text(s: State) -> str:
    return "image_prep" if s.get("scanned_pages") else "segment"


def route_after_classify(s: State) -> str:
    return "sheet_map" if s.get("sheet_kind") == "tabular" else "segment"


def route_after_transform(s: State, cfg: Config) -> str:
    if s.get("error"):
        return "finalize"
    if s.get("parse_ok"):
        return "validate"
    # Fix 11: MAPPING_MAX_RETRIES = 1 means exactly one retry is allowed.
    # mapping_retries is incremented by sheet_transform before this router runs,
    # so > MAPPING_MAX_RETRIES means we have exhausted all retries.
    if s.get("mapping_retries", 0) > cfg.MAPPING_MAX_RETRIES:
        return "finalize"
    return "sheet_map"


def route_after_validate(s: State) -> str:
    # Fix 1: sheets must never go to vision_extract (no page images available).
    if s.get("error"):
        return "finalize"
    if s.get("file_type") == "sheet":
        return "finalize"
    if s.get("vision_queue") and not s.get("escalated"):
        return "vision_extract"
    return "finalize"
