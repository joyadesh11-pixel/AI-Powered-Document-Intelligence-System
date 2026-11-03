"""Tests for the ocr node — T1–T8 + T_nodup.

image_prep is run first to produce page_images, then ocr is called directly.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from app.pipeline.config import get_config
from app.pipeline.nodes.image_prep import make_image_prep
from app.pipeline.nodes.ocr import ocr
from app.pipeline.state import Deps


# ------------------------------------------------------------------ helpers

def _image_prep_deps(tmp_path: Path) -> Deps:
    cfg = get_config()
    return Deps(config=cfg, llm=None, seen_store=None, mapping_cache=None,
                image_temp_dir=str(tmp_path))


def _prep_page(fx_path: str, tmp_path: Path) -> dict:
    """Run image_prep on a single image file; return the state dict."""
    deps = _image_prep_deps(tmp_path)
    fn = make_image_prep(deps)
    return fn({
        "file_path": fx_path,
        "file_type": "image",
        "page_count": 1,
        "sim": {},
    })


def _ocr_state(prep_result: dict, **extra) -> dict:
    """Build an ocr-ready state from image_prep result."""
    s: dict[str, Any] = {
        "page_images": prep_result.get("page_images", {}),
        "low_conf_pages": prep_result.get("low_conf_pages", []),
        "scanned_pages": prep_result.get("scanned_pages", [0]),
        "page_text": {},
        "page_source": {},
        "ocr_conf": {},
        "warnings": [],
        "sim": {},
    }
    s.update(extra)
    return s


# ------------------------------------------------------------------ T1

def test_t1_scan_clean_high_conf_and_known_fields(fx, tmp_path):
    """T1: scan_clean.png → conf >= 0.9; SCAN-0001 and 99.00 found in page_text."""
    prep = _prep_page(str(fx["scan_clean"]), tmp_path)
    assert 0 not in prep.get("low_conf_pages", []), (
        "scan_clean should not be low_conf after image_prep"
    )
    state = _ocr_state(prep)
    result = ocr(state)

    conf = result.get("ocr_conf", {}).get(0, 0.0)
    text = result.get("page_text", {}).get(0, "")

    assert conf >= 0.9, f"expected conf >= 0.9, got {conf:.4f}; text snippet: {text[:200]!r}"
    assert "SCAN-0001" in text, f"invoice number not found; text: {text[:400]!r}"
    assert "99.00" in text, f"total not found; text: {text[:400]!r}"


# ------------------------------------------------------------------ T2

def test_t2_line_item_intact(fx, tmp_path):
    """T2: Widget line item (description + qty + price + amount) on one output line."""
    prep = _prep_page(str(fx["scan_clean"]), tmp_path)
    state = _ocr_state(prep)
    result = ocr(state)
    text = result.get("page_text", {}).get(0, "")

    # Check all four fields appear on the same line
    for line in text.splitlines():
        if "Widget" in line:
            assert "10" in line, f"qty not on Widget line: {line!r}"
            assert "5.00" in line, f"unit_price not on Widget line: {line!r}"
            assert "50.00" in line, f"amount not on Widget line: {line!r}"
            break
    else:
        pytest.fail(f"'Widget' not found in any line of OCR output. Full text:\n{text}")


# ------------------------------------------------------------------ T3

def test_t3_rotated_90_recovered(fx, tmp_path):
    """T3: rotated_90.png → orientation retry recovers; page 0 NOT in low_conf_pages."""
    prep = _prep_page(str(fx["rotated_90"]), tmp_path)
    # image_prep won't deskew 90° (outside 0.5–15° range), so page_images has the rotated image
    state = _ocr_state(prep)
    result = ocr(state)

    assert 0 not in result.get("low_conf_pages", []), (
        "rotated_90.png should be recovered by orientation retry; "
        f"page 0 ended up in low_conf_pages. conf={result.get('ocr_conf',{}).get(0)}"
    )


# ------------------------------------------------------------------ T4

def test_t4_blank_goes_to_low_conf(fx, tmp_path):
    """T4: blank.png → page 0 in low_conf_pages; LOW_OCR_CONF warning.

    image_prep may pre-flag blank.png; we clear low_conf_pages before calling ocr
    so the OCR node's own detection logic is exercised (not just the pre-flag passthrough).
    """
    prep = _prep_page(str(fx["blank"]), tmp_path)
    # Clear pre-flags so ocr must detect the blank itself
    state = _ocr_state(prep, low_conf_pages=[])
    result = ocr(state)

    assert 0 in result.get("low_conf_pages", []), "blank.png should produce low_conf_pages=[0]"
    codes = [w.get("code") if isinstance(w, dict) else getattr(w, "code", None)
             for w in result.get("warnings", [])]
    assert any("LOW_OCR_CONF" in str(c) for c in codes), (
        f"expected LOW_OCR_CONF warning, got {codes}"
    )


# ------------------------------------------------------------------ T5

def test_t5_rapidocr_raises_fallback_to_tesseract(fx, tmp_path, monkeypatch):
    """T5: RapidOCR raises → pytesseract called → text found.  Skip if no Tesseract binary."""
    import shutil
    tesseract_cmd = os.environ.get("TESSERACT_CMD", shutil.which("tesseract"))
    if not tesseract_cmd:
        pytest.skip("Tesseract binary not found; set TESSERACT_CMD to enable")

    import rapidocr_onnxruntime

    class _BadRapid:
        def __call__(self, *a, **kw):
            raise RuntimeError("injected RapidOCR failure")

    monkeypatch.setattr(rapidocr_onnxruntime, "RapidOCR", _BadRapid)
    # Clear cached module-level engine
    import app.pipeline.nodes.ocr as ocr_module
    if hasattr(ocr_module, "_ENGINE"):
        ocr_module._ENGINE.__dict__.clear()

    prep = _prep_page(str(fx["scan_clean"]), tmp_path)
    state = _ocr_state(prep)
    result = ocr(state)

    text = result.get("page_text", {}).get(0, "")
    assert "SCAN-0001" in text or len(text) > 20, (
        f"Tesseract fallback should produce some text; got: {text[:200]!r}"
    )


# ------------------------------------------------------------------ T6

def test_t6_both_engines_fail_no_exception(fx, tmp_path, monkeypatch):
    """T6: both RapidOCR and pytesseract raise → page in low_conf_pages, no exception."""
    import rapidocr_onnxruntime

    class _BadRapid:
        def __call__(self, *a, **kw):
            raise RuntimeError("RapidOCR failure")

    monkeypatch.setattr(rapidocr_onnxruntime, "RapidOCR", _BadRapid)
    import app.pipeline.nodes.ocr as ocr_module
    if hasattr(ocr_module, "_ENGINE"):
        ocr_module._ENGINE.__dict__.clear()

    try:
        import pytesseract

        def _bad_tess(*a, **kw):
            raise RuntimeError("tesseract failure")

        monkeypatch.setattr(pytesseract, "image_to_data", _bad_tess)
    except ImportError:
        pass  # pytesseract not installed, that path will raise ImportError which is also caught

    prep = _prep_page(str(fx["scan_clean"]), tmp_path)
    state = _ocr_state(prep)

    # Must not raise
    result = ocr(state)
    assert 0 in result.get("low_conf_pages", []), (
        "when both engines fail, page must go to low_conf_pages"
    )
    assert result.get("page_text", {}).get(0, "") == "", (
        "text should be empty when both engines fail"
    )


# ------------------------------------------------------------------ T7

def test_t7_existing_low_conf_pages_not_ocrd(fx, tmp_path, monkeypatch):
    """T7: page 0 pre-set in low_conf_pages → RapidOCR is never called for it."""
    import rapidocr_onnxruntime

    call_count = {"n": 0}

    orig_class = rapidocr_onnxruntime.RapidOCR

    class _SpyRapid:
        def __init__(self):
            self._inner = orig_class()

        def __call__(self, *a, **kw):
            call_count["n"] += 1
            return self._inner(*a, **kw)

    monkeypatch.setattr(rapidocr_onnxruntime, "RapidOCR", _SpyRapid)
    import app.pipeline.nodes.ocr as ocr_module
    if hasattr(ocr_module, "_ENGINE"):
        ocr_module._ENGINE.__dict__.clear()

    prep = _prep_page(str(fx["scan_clean"]), tmp_path)
    # Inject page 0 as already low_conf
    state = _ocr_state(prep, low_conf_pages=[0])
    ocr(state)

    assert call_count["n"] == 0, (
        f"RapidOCR was called {call_count['n']} time(s) for a page already in low_conf_pages"
    )


# ------------------------------------------------------------------ T8

def test_t8_parallel_workers_preserve_order(fx, tmp_path, monkeypatch):
    """T8: 4 pages with OCR_WORKERS=2 → all 4 pages present; matches sequential output."""
    from app.pipeline.config import Config

    # Build a state with page_images pointing to 4 copies of scan_clean
    prep = _prep_page(str(fx["scan_clean"]), tmp_path)
    clean_img_path = prep["page_images"][0]

    page_images = {i: clean_img_path for i in range(4)}

    cfg_parallel = Config(OCR_WORKERS=2)
    cfg_sequential = Config(OCR_WORKERS=1)

    state_base: dict[str, Any] = {
        "page_images": page_images,
        "low_conf_pages": [],
        "scanned_pages": list(range(4)),
        "page_text": {},
        "page_source": {},
        "ocr_conf": {},
        "warnings": [],
        "sim": {},
    }

    # Patch get_config to return our configs
    import app.pipeline.nodes.ocr as ocr_module

    monkeypatch.setattr(ocr_module, "get_config", lambda: cfg_parallel)
    result_parallel = ocr({**state_base})

    monkeypatch.setattr(ocr_module, "get_config", lambda: cfg_sequential)
    result_sequential = ocr({**state_base})

    # All 4 pages present
    assert set(result_parallel["page_text"].keys()) == {0, 1, 2, 3}
    assert all(len(t) > 0 for t in result_parallel["page_text"].values()), (
        "all parallel OCR outputs should be non-empty"
    )
    # Content matches sequential
    for p in range(4):
        par_text = result_parallel["page_text"].get(p, "")
        seq_text = result_sequential["page_text"].get(p, "")
        assert par_text == seq_text, (
            f"page {p}: parallel text differs from sequential.\n"
            f"parallel: {par_text[:100]!r}\nsequential: {seq_text[:100]!r}"
        )


# ------------------------------------------------------------------ T_nodup

def test_nodup_image_prep_low_conf_not_double_reported(fx, tmp_path):
    """T_nodup: page already in low_conf_pages from image_prep must NOT get LOW_OCR_CONF."""
    prep = _prep_page(str(fx["blurry_severe"]), tmp_path)
    assert 0 in prep.get("low_conf_pages", []), "blurry_severe should be in low_conf after image_prep"

    state = _ocr_state(prep)
    result = ocr(state)

    # Should not have LOW_OCR_CONF for page 0 (it was skipped by ocr)
    codes = [w.get("code") if isinstance(w, dict) else getattr(w, "code", None)
             for w in result.get("warnings", [])]
    assert not any("LOW_OCR_CONF" in str(c) for c in codes), (
        f"page 0 was pre-set as low_conf; ocr must not add LOW_OCR_CONF again. Got: {codes}"
    )
    # page 0 should not appear in page_text as an ocr-processed page
    assert 0 not in result.get("page_text", {}), (
        "page 0 should not be OCR'd when it was already in low_conf_pages"
    )
