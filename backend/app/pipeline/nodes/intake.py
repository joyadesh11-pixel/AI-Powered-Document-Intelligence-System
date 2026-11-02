"""intake node — classify file type, validate constraints, compute hash.

Reads file_path. Writes file_type, file_hash, size_bytes, page_count, warnings;
on failure: error_code, error, status="failed".

make_intake(deps) is a factory so Config is closed over (fix 6 pattern).
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

from app.pipeline.config import Config, get_config
from app.pipeline.state import Deps, State, node
from app.pipeline.utils.hashing import sha256_file
from app.schemas.models import ErrorCode, IssueCode, Severity


# ------------------------------------------------------------------ magic byte signatures

_MAGIC_PDF  = b"%PDF"
_MAGIC_JPG  = b"\xff\xd8\xff"
_MAGIC_PNG  = b"\x89PNG"
_MAGIC_TIFF_LE = b"II*\x00"
_MAGIC_TIFF_BE = b"MM\x00*"
_MAGIC_ZIP  = b"PK\x03\x04"
_MAGIC_OLE  = b"\xd0\xcf\x11\xe0"

_IMAGE_EXT  = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
_SHEET_EXT  = {".xlsx", ".xls", ".csv"}


def _issue(code: IssueCode, msg: str) -> dict:
    return {
        "code": code.value,
        "severity": Severity.info.value,
        "field": None,
        "expected": None,
        "got": None,
        "message": msg,
        "location": {},
    }


def _fail(code: ErrorCode, msg: str) -> dict:
    return {
        "error_code": code.value,
        "error": msg,
        "status": "failed",
    }


def _detect_type(header: bytes, first8k: bytes, ext: str) -> str | None:
    """Return detected file type string or None for unsupported."""
    if header[:4] == _MAGIC_PDF:
        return "pdf"
    if header[:3] == _MAGIC_JPG:
        return "image"
    if header[:4] == _MAGIC_PNG:
        return "image"
    if header[:4] in (_MAGIC_TIFF_LE, _MAGIC_TIFF_BE):
        return "image"
    if header[:4] == _MAGIC_ZIP:
        return "zip_maybe"  # may be xlsx or docx — open test decides
    if header[:4] == _MAGIC_OLE:
        return "ole_maybe"  # may be xls or doc — open test decides
    # CSV: no NUL bytes in first 8 KB and extension is .csv
    if ext == ".csv" and b"\x00" not in first8k:
        return "sheet"
    return None


def _ext_file_type(ext: str) -> str | None:
    """Extension-based expected type (for mismatch check)."""
    if ext == ".pdf":
        return "pdf"
    if ext in _IMAGE_EXT:
        return "image"
    if ext in _SHEET_EXT:
        return "sheet"
    return None


def make_intake(deps: Deps | None = None):
    """Return an intake node with Config closed over."""
    config: Config = (deps or {}).get("config") or get_config()

    @node("intake")
    def intake(s: State) -> dict:
        path_str: str = s["file_path"]
        path = Path(path_str)
        ext = path.suffix.lower()
        warnings: list[dict] = []

        # ---- 1. stat ----
        try:
            stat = os.stat(path_str)
        except OSError as exc:
            return _fail(ErrorCode.CORRUPT_FILE, f"intake: cannot stat file: {exc}")

        size_bytes = stat.st_size
        if size_bytes == 0:
            return _fail(ErrorCode.EMPTY_FILE, "intake: file is empty (0 bytes)")
        if size_bytes > config.MAX_FILE_MB * 1024 * 1024:
            return _fail(ErrorCode.TOO_LARGE,
                         f"intake: file {size_bytes} bytes exceeds {config.MAX_FILE_MB} MB limit")

        # ---- 2. magic bytes ----
        try:
            with open(path_str, "rb") as fh:
                header = fh.read(16)
                fh.seek(0)
                first8k = fh.read(8192)
        except OSError as exc:
            return _fail(ErrorCode.CORRUPT_FILE, f"intake: cannot read file: {exc}")

        detected = _detect_type(header, first8k, ext)
        if detected is None:
            return _fail(ErrorCode.UNSUPPORTED_TYPE,
                         f"intake: unsupported file type (ext={ext!r}, magic={header[:4]!r})")

        # ---- extension mismatch check ----
        expected_from_ext = _ext_file_type(ext)
        actual_type: str  # resolved after open test

        # ---- 3. hash (streaming) ----
        try:
            file_hash = sha256_file(path_str)
        except OSError as exc:
            return _fail(ErrorCode.CORRUPT_FILE, f"intake: cannot hash file: {exc}")

        # ---- 4. open test ----
        page_count = 0

        if detected == "pdf":
            actual_type = "pdf"
            try:
                doc = fitz.open(path_str)
            except Exception as exc:
                return _fail(ErrorCode.CORRUPT_FILE, f"intake: PDF cannot be opened: {exc}")
            if doc.needs_pass:
                ok = doc.authenticate("")
                doc.close()
                if not ok:
                    return _fail(ErrorCode.ENCRYPTED, "intake: PDF is password-protected")
            page_count = doc.page_count
            doc.close()
            if page_count == 0:
                return _fail(ErrorCode.CORRUPT_FILE, "intake: PDF has 0 pages")
            if page_count > config.MAX_PAGES:
                return _fail(ErrorCode.TOO_MANY_PAGES,
                             f"intake: PDF has {page_count} pages (max {config.MAX_PAGES})")

        elif detected == "image":
            actual_type = "image"
            from PIL import Image, ImageFile, UnidentifiedImageError
            try:
                img = Image.open(path_str)
                img.load()
                n_frames = getattr(img, "n_frames", 1)
                page_count = n_frames
            except (OSError, UnidentifiedImageError, Exception):
                # Retry with truncated-images flag
                original_flag = ImageFile.LOAD_TRUNCATED_IMAGES
                ImageFile.LOAD_TRUNCATED_IMAGES = True
                try:
                    img = Image.open(path_str)
                    img.load()
                    n_frames = getattr(img, "n_frames", 1)
                    page_count = n_frames
                    warnings.append(_issue(IssueCode.TRUNCATED_IMAGE,
                                           "intake: image loaded with truncated-images flag"))
                except Exception as exc2:
                    return _fail(ErrorCode.CORRUPT_FILE, f"intake: image cannot be opened: {exc2}")
                finally:
                    ImageFile.LOAD_TRUNCATED_IMAGES = original_flag

        elif detected == "sheet":
            actual_type = "sheet"
            # Already confirmed text CSV — check encoding
            _found_enc = False
            for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
                try:
                    with open(path_str, encoding=enc, errors="strict") as fh:
                        content = fh.read()
                    if content.strip():
                        _found_enc = True
                        break
                except (UnicodeDecodeError, OSError):
                    continue
            if not _found_enc:
                return _fail(ErrorCode.CORRUPT_FILE, "intake: CSV cannot be decoded")
            page_count = 0

        elif detected == "zip_maybe":
            # Could be xlsx or docx/unsupported
            try:
                with zipfile.ZipFile(path_str, "r") as zf:
                    names = zf.namelist()
                    total_unzipped = sum(i.file_size for i in zf.infolist())
                    if total_unzipped > config.MAX_UNZIPPED_MB * 1024 * 1024:
                        return _fail(ErrorCode.TOO_LARGE,
                                     f"intake: declared unzipped size {total_unzipped} bytes "
                                     f"exceeds {config.MAX_UNZIPPED_MB} MB limit")
                    has_workbook = "xl/workbook.xml" in names
            except zipfile.BadZipFile as exc:
                return _fail(ErrorCode.CORRUPT_FILE, f"intake: ZIP/XLSX is corrupt: {exc}")
            except OSError as exc:
                return _fail(ErrorCode.CORRUPT_FILE, f"intake: cannot open ZIP: {exc}")

            if not has_workbook:
                return _fail(ErrorCode.UNSUPPORTED_TYPE,
                             f"intake: ZIP file is not an XLSX (no xl/workbook.xml); ext={ext!r}")

            # Try loading as XLSX
            try:
                import openpyxl
                wb = openpyxl.load_workbook(path_str, read_only=True, data_only=True)
                wb.close()
            except Exception as exc:
                return _fail(ErrorCode.CORRUPT_FILE, f"intake: XLSX workbook cannot be loaded: {exc}")

            actual_type = "sheet"
            page_count = 0

        elif detected == "ole_maybe":
            # Could be xls or doc
            try:
                import xlrd
                xlrd.open_workbook(path_str)
            except Exception as exc:
                return _fail(ErrorCode.UNSUPPORTED_TYPE,
                             f"intake: OLE file is not a valid XLS: {exc}")
            actual_type = "sheet"
            page_count = 0

        else:
            return _fail(ErrorCode.UNSUPPORTED_TYPE,
                         f"intake: cannot handle detected type {detected!r}")

        # ---- extension mismatch (compare actual vs expected from extension) ----
        if expected_from_ext is not None and expected_from_ext != actual_type:
            warnings.append(_issue(
                IssueCode.EXT_MISMATCH,
                f"intake: extension {ext!r} suggests {expected_from_ext!r} "
                f"but magic bytes indicate {actual_type!r}",
            ))

        return {
            "file_type": actual_type,
            "file_hash": file_hash,
            "size_bytes": size_bytes,
            "page_count": page_count,
            "warnings": warnings,
        }

    return intake
