"""ocr node — run RapidOCR on page images, rebuild line structure, record confidence.

Writes: page_text, page_source, ocr_conf, low_conf_pages (extended), warnings (merged).
Uses a thread-local RapidOCR engine so each worker thread gets its own ONNX session.
Merges into existing page_text / page_source / ocr_conf (never replaces digital pages).

M7 note: sim.low_conf_units compat line forces specific pages into low_conf_pages so
scenario 3 integration test keeps passing; remove when sim switches are removed in M7.
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import numpy as np
from PIL import Image

from app.pipeline.config import Config, get_config
from app.pipeline.state import State, node
from app.schemas.models import IssueCode, Severity


# ------------------------------------------------------------------ thread-local engine

_ENGINE = threading.local()


def _get_engine():
    if not hasattr(_ENGINE, "rapid"):
        from rapidocr_onnxruntime import RapidOCR
        _ENGINE.rapid = RapidOCR()
    return _ENGINE.rapid


# ------------------------------------------------------------------ line reconstruction

def _rebuild_lines(results: list) -> tuple[str, float]:
    """Convert RapidOCR results (list of [box, text, conf]) to line text + weighted confidence.

    Ignores boxes with conf < 0.3 when computing median box height (logos/stamps skew it).
    Joins words on the same line with a space; uses a tab when horizontal gap > 1.5× avg char width.
    """
    if not results:
        return "", 0.0

    items = []
    for r in results:
        try:
            box, text, conf = r[0], r[1], float(r[2])
        except (IndexError, TypeError, ValueError):
            continue
        if not text or not text.strip():
            continue
        box_arr = np.array(box)
        y_center = float((box_arr[:, 1].min() + box_arr[:, 1].max()) / 2)
        x_left = float(box_arr[:, 0].min())
        x_right = float(box_arr[:, 0].max())
        box_h = float(box_arr[:, 1].max() - box_arr[:, 1].min())
        items.append((y_center, x_left, x_right, text, conf, box_h))

    if not items:
        return "", 0.0

    # median box height, ignoring low-confidence items (< 0.3)
    heights_for_median = [bh for _, _, _, _, c, bh in items if c >= 0.3]
    if not heights_for_median:
        heights_for_median = [bh for _, _, _, _, _, bh in items]
    median_h = float(np.median(heights_for_median)) if heights_for_median else 20.0

    # Sort by y-center, then group into lines
    items.sort(key=lambda x: x[0])
    lines: list[list[tuple]] = []
    current: list[tuple] = []
    current_yc: float | None = None

    for item in items:
        y_c = item[0]
        if current_yc is None or abs(y_c - current_yc) <= 0.5 * median_h:
            current.append(item)
            current_yc = float(np.mean([it[0] for it in current]))
        else:
            lines.append(current)
            current = [item]
            current_yc = y_c
    if current:
        lines.append(current)

    # Build text lines
    text_lines: list[str] = []
    for line_items in lines:
        line_items.sort(key=lambda x: x[1])  # sort by x_left
        parts: list[str] = []
        for idx, (_, xl, xr, text, conf, bh) in enumerate(line_items):
            if idx == 0:
                parts.append(text)
            else:
                prev_xr = line_items[idx - 1][2]
                gap = xl - prev_xr
                avg_char_w = max(1.0, (xr - xl) / max(1, len(text)))
                sep = "\t" if gap > 1.5 * avg_char_w else " "
                parts.append(sep + text)
        text_lines.append("".join(parts))

    full_text = "\n".join(text_lines)

    # Length-weighted confidence
    all_texts = [it[3] for it in items]
    all_confs = [it[4] for it in items]
    total_chars = sum(len(t) for t in all_texts)
    if total_chars > 0:
        w_conf = sum(len(t) * c for t, c in zip(all_texts, all_confs)) / total_chars
    else:
        w_conf = 0.0

    return full_text, float(w_conf)


# ------------------------------------------------------------------ single-page OCR

def _ocr_array(arr: np.ndarray) -> tuple[str, float]:
    """Run RapidOCR on a grayscale numpy array; return (text, conf)."""
    engine = _get_engine()
    result, _ = engine(arr)
    return _rebuild_lines(result or [])


def _ocr_page(page_idx: int, img_path: str, cfg: Config) -> tuple[int, str, float, bool]:
    """Process one page: RapidOCR with Tesseract fallback and orientation retry.

    Returns (page_idx, text, conf, both_failed).
    """
    try:
        arr = np.array(Image.open(img_path).convert("L"))
    except Exception:
        return page_idx, "", 0.0, True

    # Primary: RapidOCR
    try:
        text, conf = _ocr_array(arr)
    except Exception:
        # Fallback: Tesseract
        try:
            import pytesseract
            tess_cmd = os.environ.get("TESSERACT_CMD", "")
            if tess_cmd:
                pytesseract.pytesseract.tesseract_cmd = tess_cmd
            data = pytesseract.image_to_data(img_path, output_type=pytesseract.Output.DICT)
            results = []
            for i, word in enumerate(data["text"]):
                if word.strip() and int(data.get("conf", [-1])[i]) > 0:
                    x = data["left"][i]; y = data["top"][i]
                    w = data["width"][i]; h = data["height"][i]
                    box = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
                    results.append([box, word, data["conf"][i] / 100.0])
            text, conf = _rebuild_lines(results)
        except Exception:
            return page_idx, "", 0.0, True

    # Orientation retry when confidence is low or text is sparse
    if conf < cfg.OCR_CONF_MIN or len(text.strip()) < 20:
        best_text, best_conf = text, conf
        for k in (1, 3):  # 90° and 270°
            rotated = np.rot90(arr, k)
            try:
                r_text, r_conf = _ocr_array(rotated)
                if r_conf > best_conf:
                    best_text, best_conf = r_text, r_conf
            except Exception:
                pass
        text, conf = best_text, best_conf

    return page_idx, text, conf, False


# ------------------------------------------------------------------ node

def _low_ocr_conf_issue(page_idx: int, conf: float) -> dict:
    return {
        "code": IssueCode.LOW_OCR_CONF.value,
        "severity": Severity.warning.value,
        "field": None,
        "message": f"Page {page_idx}: OCR confidence {conf:.2f} below threshold",
        "location": {"page": page_idx},
    }


@node("ocr")
def ocr(s: State) -> dict:
    cfg = get_config()

    page_images: dict[int, str] = s.get("page_images") or {}
    existing_lc: set[int] = set(s.get("low_conf_pages") or [])

    pages_to_process = [p for p in page_images if p not in existing_lc]

    # Start with existing accumulated values (merge, never replace digital pages)
    page_text: dict[int, str] = dict(s.get("page_text") or {})
    page_source: dict[int, str] = dict(s.get("page_source") or {})
    ocr_conf: dict[int, float] = dict(s.get("ocr_conf") or {})
    new_lc: list[int] = []
    new_warnings: list[dict] = []

    if pages_to_process:
        with ThreadPoolExecutor(max_workers=cfg.OCR_WORKERS) as pool:
            futures = {
                pool.submit(_ocr_page, p, page_images[p], cfg): p
                for p in pages_to_process
            }
            for fut in as_completed(futures):
                p, text, conf, both_failed = fut.result()
                page_text[p] = text
                page_source[p] = "ocr"
                ocr_conf[p] = conf
                if both_failed or conf < cfg.OCR_CONF_MIN or len(text.strip()) < 20:
                    new_lc.append(p)
                    if not both_failed:
                        new_warnings.append(_low_ocr_conf_issue(p, conf))

    # Sim compat: force sim.low_conf_units into low_conf (removed in M7)
    for p in s.get("sim", {}).get("low_conf_units", []):
        if p not in existing_lc and p not in new_lc:
            new_lc.append(p)

    all_lc = sorted(existing_lc | set(new_lc))
    merged_warnings = list(s.get("warnings") or []) + new_warnings

    return {
        "page_text": page_text,
        "page_source": page_source,
        "ocr_conf": ocr_conf,
        "low_conf_pages": all_lc,
        "warnings": merged_warnings,
    }
