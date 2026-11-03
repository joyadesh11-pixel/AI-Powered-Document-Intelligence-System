"""Ingest service: validate, deduplicate, and store uploaded files.

save_upload() reuses the pipeline's intake magic-byte detection so the
allow-list is never duplicated.  The user-supplied filename is sanitised
before use as a display label; the file on disk is stored under its sha256
hash (no user path component ever reaches the filesystem).
"""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import Document
from app.pipeline.config import get_config
from app.pipeline.nodes.intake import _detect_type  # reuse magic-byte allow-list


_UPLOAD_DIR = Path(__file__).resolve().parents[2] / "data" / "uploads"


def _sanitise_filename(name: str) -> str:
    """Strip unsafe characters; keep extension; cap length at 200 chars."""
    # Normalise unicode, replace path separators and control chars
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    name = name.strip(". ")
    return name[:200] or "upload"


@dataclass
class IngestResult:
    document: Document
    cached: bool  # True when the hash already existed


class IngestError(Exception):
    """Raised for files that fail magic-byte or size checks."""
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def save_upload(filename: str, data: bytes, session: Session) -> IngestResult:
    """Validate, hash, and persist an uploaded file.

    Returns the Document row (existing or new) plus a `cached` flag.
    Raises IngestError for oversized or unsupported files.
    No LLM or pipeline call is made here.
    """
    cfg = get_config()

    # --- size check (streaming-friendly: data is already in memory here,
    #     but the check happens before any disk write)
    max_bytes = cfg.MAX_FILE_MB * 1024 * 1024
    if len(data) == 0:
        raise IngestError("File is empty", "EMPTY_FILE")
    if len(data) > max_bytes:
        raise IngestError(
            f"File size {len(data)} bytes exceeds {cfg.MAX_FILE_MB} MB limit",
            "TOO_LARGE",
        )

    # --- magic-byte check (reuses intake's allow-list)
    header = data[:16]
    first8k = data[:8192]
    ext = Path(filename).suffix.lower()
    detected = _detect_type(header, first8k, ext)
    if detected is None:
        raise IngestError(
            f"Unsupported file type (ext={ext!r}, magic={header[:4]!r})",
            "UNSUPPORTED_TYPE",
        )

    # --- hash
    file_hash = hashlib.sha256(data).hexdigest()

    # --- deduplication: return existing document if hash already stored
    existing = session.query(Document).filter_by(file_hash=file_hash).first()
    if existing is not None:
        return IngestResult(document=existing, cached=True)

    # --- persist file under hash (user filename never used in path)
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = _UPLOAD_DIR / file_hash
    dest.write_bytes(data)

    # --- insert document row
    display_name = _sanitise_filename(filename)
    doc = Document(
        filename=display_name,
        file_hash=file_hash,
        file_type=detected if detected not in ("zip_maybe", "ole_maybe") else "sheet",
        size_bytes=len(data),
        status="queued",
    )
    session.add(doc)
    session.flush()  # assign id without committing

    return IngestResult(document=doc, cached=False)
