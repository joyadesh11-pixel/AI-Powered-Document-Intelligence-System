"""pdf_triage node — classify each PDF page as digital, scanned, or garbled."""
from __future__ import annotations

import re

import pymupdf

from app.pipeline.config import Config, get_config
from app.pipeline.state import State, node


def _garbled_ratio(text: str) -> float:
    """Fraction of chars that are replacement chars, BMP private-use, or control chars."""
    if not text:
        return 0.0
    cleaned = re.sub(r"\(cid:\d+\)", "�", text)
    bad = sum(
        1 for c in cleaned
        if c == "�"
        or ("" <= c <= "")
        or (ord(c) < 32 and c not in "\n\r\t")
    )
    return bad / len(cleaned) if cleaned else 0.0


def _img_cover(page: pymupdf.Page) -> float:
    """Fraction of page area covered by raster images."""
    area = page.rect.width * page.rect.height
    if not area:
        return 0.0
    return sum(
        (b[2] - b[0]) * (b[3] - b[1])
        for info in page.get_image_info()
        for b in [info.get("bbox", [])]
        if len(b) == 4
    ) / area


def _classify(page: pymupdf.Page, cfg: Config) -> str:
    text = page.get_text().strip()
    chars = len(text)
    # Rule 1: almost no text → image-only or blank
    if chars < cfg.PAGE_MIN_CHARS:
        return "scanned"
    # Rule 2: high proportion of undecodable chars → garbled font
    if _garbled_ratio(text) > cfg.GARBLED_RATIO_MAX:
        return "garbled"
    # Rule 3: large image + very little stray text → scanned with page number
    if _img_cover(page) > cfg.IMG_COVER_MAX and chars < cfg.SCAN_TEXT_MAX_CHARS:
        return "scanned"
    return "digital"


@node("pdf_triage")
def pdf_triage(s: State) -> dict:
    cfg = get_config()
    n = s["page_count"]
    page_labels: dict[int, str] = {}
    try:
        with pymupdf.open(s["file_path"]) as doc:
            for p in range(n):
                try:
                    page_labels[p] = _classify(doc[p], cfg)
                except Exception:
                    page_labels[p] = "scanned"
    except Exception:
        page_labels = {p: "scanned" for p in range(n)}

    digital = [p for p, lbl in page_labels.items() if lbl == "digital"]
    scanned = [p for p, lbl in page_labels.items() if lbl != "digital"]
    return {"page_labels": page_labels, "digital_pages": digital, "scanned_pages": scanned}
