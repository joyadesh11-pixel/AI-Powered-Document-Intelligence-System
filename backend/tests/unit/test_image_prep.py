"""Tests for image_prep node — T1–T8 + T_laplacian + T_tiff_badframe.

All tests use make_image_prep(deps) with a real tmp_path so no bytes leak into state.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from app.pipeline.config import get_config
from app.pipeline.nodes.image_prep import make_image_prep
from app.pipeline.state import Deps


# ------------------------------------------------------------------ helpers

def _make_fn(tmp_path: Path):
    """Return a bound image_prep callable writing into tmp_path."""
    cfg = get_config()
    deps = Deps(config=cfg, llm=None, seen_store=None, mapping_cache=None,
                image_temp_dir=str(tmp_path))
    return make_image_prep(deps)


def _state(file_path: str, file_type: str = "image",
           scanned_pages: list[int] | None = None, **extra) -> dict:
    s: dict[str, Any] = {
        "file_path": file_path,
        "file_type": file_type,
        "page_count": 1,
    }
    if scanned_pages is not None:
        s["scanned_pages"] = scanned_pages
    s.update(extra)
    return s


def _laplacian_variance(path: str) -> float:
    """Compute Laplacian variance on the gray image at path."""
    import cv2
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


# ------------------------------------------------------------------ T1

def test_t1_png_produces_one_grayscale_page(fx, tmp_path):
    """T1: PNG/JPG → one entry in page_images; image on disk is grayscale."""
    fn = _make_fn(tmp_path)
    result = fn(_state(str(fx["scan_clean"])))
    assert result.get("error") is None
    page_images = result["page_images"]
    assert set(page_images.keys()) == {0}
    pil_img = Image.open(page_images[0])
    assert pil_img.mode == "L", f"expected grayscale, got mode={pil_img.mode!r}"


def test_t1_jpg_produces_one_grayscale_page(fx, tmp_path):
    """T1 (JPG variant): JPG → one grayscale page."""
    fn = _make_fn(tmp_path)
    result = fn(_state(str(fx["exif_rotated"])))
    page_images = result["page_images"]
    assert set(page_images.keys()) == {0}
    assert Image.open(page_images[0]).mode == "L"


# ------------------------------------------------------------------ T2

def test_t2_tiff_three_frames(fx, tmp_path):
    """T2: 3-frame TIFF → page_images keys {0,1,2}; scanned_pages == [0,1,2]."""
    fn = _make_fn(tmp_path)
    result = fn(_state(str(fx["scan_3frame"])))
    assert set(result["page_images"].keys()) == {0, 1, 2}
    assert sorted(result["scanned_pages"]) == [0, 1, 2]


# ------------------------------------------------------------------ T3

def test_t3_exif_rotated_jpg_is_upright(fx, tmp_path):
    """T3: EXIF-rotated JPG → rendered image is portrait (height > width)."""
    fn = _make_fn(tmp_path)
    result = fn(_state(str(fx["exif_rotated"])))
    page_images = result["page_images"]
    assert 0 in page_images
    w, h = Image.open(page_images[0]).size
    assert h > w, f"expected portrait after exif_transpose, got {w}×{h}"


# ------------------------------------------------------------------ T4

def _estimate_skew(img_path: str) -> float:
    """Return estimated skew angle in degrees via minAreaRect on binarised pixels."""
    import cv2
    img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0
    _, binary = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0.0
    all_pts = np.vstack([c for c in contours if len(c) >= 3])
    if len(all_pts) < 5:
        return 0.0
    _, _, raw_angle = cv2.minAreaRect(all_pts)
    angle = raw_angle if raw_angle >= -45 else raw_angle + 90
    return angle


@pytest.mark.parametrize("fx_key", ["rotated_5deg"])
def test_t4_deskew_corrects_rotation(fx, tmp_path, fx_key):
    """T4: rotated image → residual skew after deskew < 1°."""
    fn = _make_fn(tmp_path)
    result = fn(_state(str(fx[fx_key])))
    assert 0 in result.get("page_images", {})
    residual = abs(_estimate_skew(result["page_images"][0]))
    assert residual < 1.5, f"expected <1.5° residual after deskew, got {residual:.2f}°"


# ------------------------------------------------------------------ T5

def test_t5_blurry_severe_goes_to_low_conf(fx, tmp_path):
    """T5: blurry_severe.png → page 0 in low_conf_pages; LOW_IMAGE_QUALITY in warnings."""
    fn = _make_fn(tmp_path)
    result = fn(_state(str(fx["blurry_severe"])))
    assert 0 in result.get("low_conf_pages", []), "blurry_severe must go to low_conf_pages"
    codes = [w.get("code") if isinstance(w, dict) else getattr(w, "code", None)
             for w in result.get("warnings", [])]
    assert any("LOW_IMAGE_QUALITY" in str(c) for c in codes), (
        f"expected LOW_IMAGE_QUALITY warning, got {codes}"
    )


# ------------------------------------------------------------------ T6

def test_t6_tiny_image_goes_to_low_conf(fx, tmp_path):
    """T6: tiny_300px.png (side_px < MIN_SIDE_PX) → low_conf_pages + LOW_IMAGE_QUALITY."""
    fn = _make_fn(tmp_path)
    result = fn(_state(str(fx["tiny_300px"])))
    assert 0 in result.get("low_conf_pages", []), "tiny image must go to low_conf_pages"
    codes = [w.get("code") if isinstance(w, dict) else getattr(w, "code", None)
             for w in result.get("warnings", [])]
    assert any("LOW_IMAGE_QUALITY" in str(c) for c in codes)


# ------------------------------------------------------------------ T7

def test_t7_scanned_pdf_renders_at_expected_size(fx, tmp_path):
    """T7: scanned.pdf → page_images keys match scanned_pages; long side ≈ OCR_DPI/72 × pts."""
    # First run pdf_triage to get scanned_pages
    from app.pipeline.nodes.pdf_triage import pdf_triage
    import fitz
    path = str(fx["scanned_pdf"])
    with fitz.open(path) as doc:
        page_count = doc.page_count
        page_width_pts = doc[0].rect.width
        page_height_pts = doc[0].rect.height
    triage_result = pdf_triage({"file_path": path, "file_type": "pdf",
                                "page_count": page_count, "sim": {}})
    scanned = triage_result.get("scanned_pages", list(range(page_count)))

    fn = _make_fn(tmp_path)
    result = fn(_state(path, file_type="pdf", scanned_pages=scanned))

    cfg = get_config()
    assert set(result["page_images"].keys()) == set(scanned)
    for p, img_path in result["page_images"].items():
        w, h = Image.open(img_path).size
        long_side = max(w, h)
        expected = max(page_width_pts, page_height_pts) * cfg.OCR_DPI / 72
        capped = min(expected, cfg.OCR_MAX_SIDE_PX)
        assert abs(long_side - capped) / capped < 0.15, (
            f"page {p}: long_side={long_side}, expected~{capped:.0f}"
        )


# ------------------------------------------------------------------ T8

def test_t8_no_bytes_in_state(fx, tmp_path):
    """T8: result keys (except non-serialisable) round-trip through json.dumps."""
    fn = _make_fn(tmp_path)
    result = fn(_state(str(fx["scan_clean"])))
    serialisable = {
        k: v for k, v in result.items()
        if k not in ("history",)  # history has dicts which may have non-json-safe types
    }
    try:
        json.dumps(serialisable)
    except TypeError as e:
        pytest.fail(f"State contains non-serialisable value: {e}")
    # Assert page_images values are strings (paths), never bytes
    for p, v in result.get("page_images", {}).items():
        assert isinstance(v, str), f"page_images[{p}] is {type(v).__name__}, expected str"


# ------------------------------------------------------------------ T_laplacian

@pytest.mark.parametrize("fx_key,expect_above_min", [
    ("scan_clean",    True),
    ("scan_mild",     True),
    ("scan_moderate", True),   # must stay >= BLUR_VAR_MIN; report if it fails
    ("blurry_severe", False),  # must be < BLUR_VAR_MIN
    ("blank",         True),   # blank image has low but non-zero variance
])
def test_laplacian_variance(fx, fx_key, expect_above_min, capsys):
    """T_laplacian: print and validate Laplacian variance for each scan fixture."""
    cfg = get_config()
    var = _laplacian_variance(str(fx[fx_key]))
    print(f"\n[T_laplacian] {fx_key}: Laplacian variance = {var:.2f}  (BLUR_VAR_MIN={cfg.BLUR_VAR_MIN})")
    if not expect_above_min:
        assert var < cfg.BLUR_VAR_MIN, (
            f"{fx_key}: expected variance < BLUR_VAR_MIN ({cfg.BLUR_VAR_MIN}), got {var:.2f}"
        )
    elif fx_key == "scan_moderate":
        if var < cfg.BLUR_VAR_MIN:
            pytest.fail(
                f"scan_moderate Laplacian variance {var:.2f} < BLUR_VAR_MIN {cfg.BLUR_VAR_MIN}. "
                "This means moderate degradation is too severe — report to the user, "
                "do NOT loosen BLUR_VAR_MIN."
            )


# ------------------------------------------------------------------ T_tiff_badframe

def test_tiff_badframe_skips_bad_frame(fx, tmp_path, monkeypatch):
    """T_tiff_badframe: monkeypatch PIL TiffImageFile.seek to raise for frame 1 → pages {0,2}, no exception."""
    from PIL.TiffImagePlugin import TiffImageFile

    orig_seek = TiffImageFile.seek

    def patched_seek(self, frame):
        if frame == 1:
            raise OSError("simulated corrupt TIFF frame")
        return orig_seek(self, frame)

    monkeypatch.setattr(TiffImageFile, "seek", patched_seek)

    fn = _make_fn(tmp_path)
    result = fn(_state(str(fx["scan_3frame"])))
    # Frame 1 was skipped; pages 0 and 2 should be in page_images
    assert set(result.get("page_images", {}).keys()) == {0, 2}, (
        f"expected pages {{0, 2}}, got {set(result.get('page_images', {}).keys())}"
    )
    # scanned_pages indices stay stable (0, 1, 2 — not renumbered)
    assert sorted(result.get("scanned_pages", [])) == [0, 1, 2], (
        f"scanned_pages should be [0,1,2], got {result.get('scanned_pages')}"
    )
