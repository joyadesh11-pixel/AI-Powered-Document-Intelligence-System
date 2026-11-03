"""image_prep node — render pages to OCR-ready grayscale PNGs, quality-score them.

Factory: make_image_prep(deps) → callable that accepts State and returns a state patch.
deps["image_temp_dir"] is the directory to write PNG files into; created by process()
with tempfile.TemporaryDirectory() so cleanup is automatic.

Writes: page_images (dict[int,str]), image_quality (dict[int,dict]),
        scanned_pages (for image files), low_conf_pages (merged), warnings (merged).
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageOps

from app.pipeline.config import Config, get_config
from app.pipeline.state import Deps, State, node
from app.schemas.models import IssueCode, Severity


# ------------------------------------------------------------------ helpers

def _quality(gray_arr: np.ndarray) -> dict:
    """Return {blur, side_px, contrast} for a grayscale numpy array."""
    blur = float(cv2.Laplacian(gray_arr, cv2.CV_64F).var())
    side_px = int(max(gray_arr.shape[0], gray_arr.shape[1]))
    contrast = float(gray_arr.std())
    return {"blur": blur, "side_px": side_px, "contrast": contrast}


def _estimate_skew(gray_arr: np.ndarray) -> float:
    """Estimate document skew angle in degrees in the range [-45, 45].

    Normalises cv2.minAreaRect's angle convention into [-45, 45] to avoid
    mistaking a 90-degree rotation for skew (OpenCV convention changed across
    versions; this normalisation is stable).
    """
    _, binary = cv2.threshold(gray_arr, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    pts = [c for c in contours if len(c) >= 3]
    if not pts:
        return 0.0
    all_pts = np.vstack(pts)
    if len(all_pts) < 5:
        return 0.0
    _, _, raw_angle = cv2.minAreaRect(all_pts)
    # Normalise: OpenCV returns angle in [-90, 0); shift into [-45, 45]
    angle = raw_angle if raw_angle >= -45 else raw_angle + 90
    return float(angle)


def _deskew(gray_arr: np.ndarray, angle: float) -> np.ndarray:
    """Rotate image by angle degrees to correct detected skew (fills border with white 255)."""
    h, w = gray_arr.shape
    M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    return cv2.warpAffine(gray_arr, M, (w, h),
                          flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT,
                          borderValue=255)


def _cap_side(pil_img: Image.Image, max_side: int) -> Image.Image:
    """Resize so the long side does not exceed max_side (Lanczos)."""
    w, h = pil_img.size
    long = max(w, h)
    if long <= max_side:
        return pil_img
    scale = max_side / long
    return pil_img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)


def _process_pil_frame(pil_img: Image.Image, cfg: Config) -> np.ndarray:
    """Apply EXIF transpose, grayscale, autocontrast, cap side, deskew."""
    pil_img = ImageOps.exif_transpose(pil_img)
    pil_img = _cap_side(pil_img, cfg.OCR_MAX_SIDE_PX)
    gray = pil_img.convert("L")
    gray = ImageOps.autocontrast(gray)
    arr = np.array(gray)
    angle = _estimate_skew(arr)
    if 0.5 <= abs(angle) <= 15.0:
        arr = _deskew(arr, angle)
    return arr


def _low_quality_issue(page_idx: int) -> dict:
    return {
        "code": IssueCode.LOW_IMAGE_QUALITY.value,
        "severity": Severity.warning.value,
        "field": None,
        "message": f"Page {page_idx}: image quality too low for reliable OCR",
        "location": {"page": page_idx},
    }


# ------------------------------------------------------------------ factory

def make_image_prep(deps: Deps | None) -> Callable[[State], dict]:
    """Return the image_prep node function closed over deps."""
    d = deps or {}

    @node("image_prep")
    def _run(s: State) -> dict:
        cfg: Config = d.get("config") or get_config()
        # Use injected temp dir if available (provided by process()); fall back to mkdtemp.
        tmp: str = d.get("image_temp_dir") or tempfile.mkdtemp(prefix="dociq_imgp_")
        tmp_path = Path(tmp)

        file_type: str = s.get("file_type", "image")
        file_path: str = s["file_path"]

        page_images: dict[int, str] = {}
        image_quality: dict[int, dict] = {}
        new_low_conf: list[int] = []
        new_warnings: list[dict] = list(s.get("warnings") or [])
        scanned_pages_out: list[int] | None = None

        if file_type == "pdf":
            pages_to_render = list(s.get("scanned_pages") or [])
            try:
                with pymupdf.open(file_path) as doc:
                    for p in pages_to_render:
                        try:
                            mat = pymupdf.Matrix(cfg.OCR_DPI / 72, cfg.OCR_DPI / 72)
                            pix = doc[p].get_pixmap(matrix=mat)
                            pil_img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                            arr = _process_pil_frame(pil_img, cfg)
                            out_path = tmp_path / f"page_{p:04d}.png"
                            Image.fromarray(arr).save(str(out_path))
                            q = _quality(arr)
                            page_images[p] = str(out_path)
                            image_quality[p] = q
                            if q["blur"] < cfg.BLUR_VAR_MIN or q["side_px"] < cfg.MIN_SIDE_PX:
                                new_low_conf.append(p)
                                new_warnings.append(_low_quality_issue(p))
                        except Exception:
                            # warn, skip this page — do not fail entire document
                            pass
            except Exception as exc:
                if not page_images:
                    raise  # all pages failed; @node wraps as INTERNAL_ERROR

        elif file_type == "image":
            try:
                pil_src = Image.open(file_path)
            except Exception as exc:
                raise RuntimeError(f"Cannot open image: {exc}") from exc

            # Iterate frames (TIFF multi-frame, or single-frame for PNG/JPG)
            frame_count = getattr(pil_src, "n_frames", 1)
            scanned_out: list[int] = []
            success_count = 0

            for i in range(frame_count):
                try:
                    pil_src.seek(i)
                    frame = pil_src.copy()
                    arr = _process_pil_frame(frame, cfg)
                    out_path = tmp_path / f"page_{i:04d}.png"
                    Image.fromarray(arr).save(str(out_path))
                    q = _quality(arr)
                    page_images[i] = str(out_path)
                    image_quality[i] = q
                    success_count += 1
                    if q["blur"] < cfg.BLUR_VAR_MIN or q["side_px"] < cfg.MIN_SIDE_PX:
                        new_low_conf.append(i)
                        new_warnings.append(_low_quality_issue(i))
                except EOFError:
                    break
                except Exception:
                    # Skip this frame; keep index stable
                    pass
                finally:
                    scanned_out.append(i)

            if success_count == 0:
                raise RuntimeError("All image frames failed to decode")

            scanned_pages_out = scanned_out

        # Merge with existing low_conf_pages
        existing_lc = set(s.get("low_conf_pages") or [])
        merged_lc = sorted(existing_lc | set(new_low_conf))

        out: dict = {
            "page_images": page_images,
            "image_quality": image_quality,
            "low_conf_pages": merged_lc,
            "warnings": new_warnings,
        }
        if scanned_pages_out is not None:
            out["scanned_pages"] = scanned_pages_out
        return out

    return _run
