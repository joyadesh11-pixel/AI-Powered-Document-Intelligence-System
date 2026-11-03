"""segment node — split page text into per-invoice units.

Real implementation for M6. Reads page_text, page_source, low_conf_pages,
tables, file_type, page_count. Writes units and warnings.
"""
from __future__ import annotations

import hashlib
import re

from app.pipeline.state import State, node
from app.schemas.models import IssueCode, Severity

_ANCHOR_RE = re.compile(
    r'(?:(?:tax\s+)?invoice|bill|receipt)\s*'
    r'(?:no|number|num|#)\.?\s*[:#]?\s*'
    r'([A-Z0-9][\w\-/]{2,})',
    re.IGNORECASE,
)
_PXY_RE = re.compile(r'page\s+(\d+)\s*(?:of|/)\s*(\d+)', re.IGNORECASE)
_TOTALS_RE = re.compile(
    r'\b(?:total\s+due|amount\s+due|balance\s+due|grand\s+total)\b',
    re.IGNORECASE,
)

_WARN_KWARGS = {"field": None, "expected": None, "got": None, "location": {}}


def _header_sig(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()][:3]
    return hashlib.md5(re.sub(r"\d", "", "".join(lines)).encode()).hexdigest()[:8]


def _extract_anchor(text: str) -> str | None:
    m = _ANCHOR_RE.search(text)
    return m.group(1).upper() if m else None


def _extract_pxy(text: str) -> tuple[int, int] | None:
    m = _PXY_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _unit_source(pages: list[int], page_source: dict[int, str]) -> str:
    srcs = {page_source.get(p, "digital") for p in pages}
    if len(srcs) > 1:
        return "mixed"
    return srcs.pop() if srcs else "digital"


def _build_unit(uid: int, pages: list[int], page_text: dict, tables: dict,
                page_source: dict, low_conf_pages: set) -> dict:
    text = "\n".join(page_text.get(p, "") for p in pages)
    tbls: list = []
    for p in pages:
        tbls.extend(tables.get(p, []))
    low_in_unit = [p for p in pages if p in low_conf_pages]
    return {
        "id": uid,
        "pages": pages,
        "text": text,
        "tables": tbls,
        "source": _unit_source(pages, page_source),
        "needs_vision": bool(low_in_unit),
        "low_conf_pages": low_in_unit,
    }


@node("segment")
def segment(s: State) -> dict:
    file_type = s.get("file_type", "pdf")
    page_text: dict[int, str] = s.get("page_text") or {}
    page_source: dict[int, str] = s.get("page_source") or {}
    tables: dict[int, list] = s.get("tables") or {}
    low_conf_pages = set(s.get("low_conf_pages") or [])
    page_count = s.get("page_count") or 0
    warnings = list(s.get("warnings") or [])

    # Determine page list
    if page_text:
        pages = sorted(page_text.keys())
    else:
        pages = list(range(page_count))

    # Sheets: one unit per sheet/page
    if file_type == "sheet":
        units = [
            _build_unit(i, [p], page_text, tables, page_source, low_conf_pages)
            for i, p in enumerate(pages)
        ]
        return {"units": units, "warnings": warnings}

    # Images: one unit per page
    if file_type == "image":
        units = [
            _build_unit(i, [p], page_text, tables, page_source, low_conf_pages)
            for i, p in enumerate(pages)
        ]
        return {"units": units, "warnings": warnings}

    if not pages:
        return {"units": [], "warnings": warnings}

    # PDF: compute per-page features
    anchors = {p: _extract_anchor(page_text.get(p, "")) for p in pages}
    pxys = {p: _extract_pxy(page_text.get(p, "")) for p in pages}
    sigs = {p: _header_sig(page_text.get(p, "")) for p in pages}
    any_anchor = any(v is not None for v in anchors.values())

    # Fallback: no anchors anywhere → one unit per page
    if not any_anchor:
        units = [
            _build_unit(i, [p], page_text, tables, page_source, low_conf_pages)
            for i, p in enumerate(pages)
        ]
        warnings.append({
            "code": IssueCode.SEGMENT_FALLBACK.value,
            "severity": Severity.info.value,
            "message": "No invoice anchors found; using one unit per page (fallback)",
            **_WARN_KWARGS,
        })
        return {"units": units, "warnings": warnings}

    # Build units from anchors / pxy markers
    unit_page_groups: list[list[int]] = []
    current: list[int] = []
    current_anchor: str | None = None

    for p in pages:
        anchor = anchors[p]
        pxy = pxys[p]

        if not current:
            current = [p]
            current_anchor = anchor
            continue

        # pxy continuation (page > 1 of N)
        if pxy and pxy[0] > 1:
            current.append(p)
            continue

        # pxy start (page 1 of N) → new unit
        if pxy and pxy[0] == 1:
            unit_page_groups.append(current)
            current = [p]
            current_anchor = anchor
            continue

        # Anchor found and differs from current unit's anchor → new unit
        if anchor is not None and current_anchor is not None and anchor != current_anchor:
            unit_page_groups.append(current)
            current = [p]
            current_anchor = anchor
            continue

        # New anchor appears where there was none before → new unit
        if anchor is not None and current_anchor is None:
            unit_page_groups.append(current)
            current = [p]
            current_anchor = anchor
            continue

        # Header sig repeats AND previous page had totals → same-template new invoice
        if (not anchor and not pxy and len(current) > 1
                and sigs[p] == sigs[current[0]]
                and _TOTALS_RE.search(page_text.get(current[-1], ""))):
            unit_page_groups.append(current)
            current = [p]
            current_anchor = None
            continue

        # Continuation
        current.append(p)
        if anchor:
            current_anchor = anchor

    if current:
        unit_page_groups.append(current)

    units = [
        _build_unit(uid, grp, page_text, tables, page_source, low_conf_pages)
        for uid, grp in enumerate(unit_page_groups)
    ]
    return {"units": units, "warnings": warnings}
