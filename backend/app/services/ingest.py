"""Ingest service: validate, deduplicate, and store uploaded files.

save_upload() streams the upload to a temp file while hashing it, so the size
cap is enforced without holding the whole file in memory.  It reuses the
pipeline's intake allow-lists (extension + magic bytes) so they are never
duplicated.  The user-supplied filename is sanitised and used only as a
display label; the file on disk is stored under its sha256 hash.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Document
from app.pipeline.config import get_config
from app.pipeline.nodes.intake import _IMAGE_EXT, _SHEET_EXT, _detect_type  # reuse allow-lists
from app.services.errors import IngestError

__all__ = ["IngestError", "IngestResult", "save_upload", "upload_dir"]

_DEFAULT_UPLOAD_DIR = Path(__file__).resolve().parents[2] / "data" / "uploads"
_ALLOWED_EXT = {".pdf"} | _IMAGE_EXT | _SHEET_EXT
_READ_CHUNK = 1024 * 1024


def upload_dir() -> Path:
    """Directory holding uploads as <sha256> files (UPLOAD_DIR env overrides)."""
    configured = get_config().UPLOAD_DIR
    return Path(configured) if configured else _DEFAULT_UPLOAD_DIR


def _sanitise_filename(name: str) -> str:
    """Strip unsafe characters; keep extension; cap length at 200 chars."""
    # Normalise unicode, replace path separators and control chars
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", name)
    name = name.strip(". ")
    return name[:200] or "upload"


@dataclass
class IngestResult:
    document: Document
    cached: bool  # True when the hash already existed


def _find_by_hash(session: Session, file_hash: str) -> Document | None:
    return session.query(Document).filter_by(file_hash=file_hash).first()


def _spool(stream: BinaryIO, tmp_path: Path, max_bytes: int) -> tuple[str, int, bytes]:
    """Copy stream to tmp_path in chunks; return (sha256, size, first 8 KB)."""
    digest = hashlib.sha256()
    size = 0
    head = b""
    with open(tmp_path, "wb") as out:
        while True:
            chunk = stream.read(_READ_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise IngestError(
                    f"File exceeds {max_bytes // (1024 * 1024)} MB limit", "TOO_LARGE"
                )
            if len(head) < 8192:
                head += chunk[: 8192 - len(head)]
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest(), size, head


def save_upload(filename: str, data: BinaryIO | bytes, session: Session) -> IngestResult:
    """Validate, hash, and persist an uploaded file.

    Returns the Document row (existing or new) plus a `cached` flag.  The new
    row is flushed, not committed: the caller commits and only then enqueues.
    Raises IngestError for empty, oversized or unsupported files.
    """
    cfg = get_config()
    stream: BinaryIO = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data

    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise IngestError(f"Unsupported file extension {ext!r}", "UNSUPPORTED_TYPE")

    root = upload_dir()
    tmp_dir = root / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / uuid.uuid4().hex
    try:
        file_hash, size, head = _spool(stream, tmp_path, cfg.MAX_FILE_MB * 1024 * 1024)
        if size == 0:
            raise IngestError("File is empty", "EMPTY_FILE")

        # magic bytes decide the type; a family mismatch (PDF named .jpg) is
        # accepted here and surfaced as a warning by the intake node
        detected = _detect_type(head[:16], head, ext)
        if detected is None:
            raise IngestError(
                f"File content does not match an allowed type (ext={ext!r})", "UNSUPPORTED_TYPE"
            )

        existing = _find_by_hash(session, file_hash)
        if existing is not None:
            return IngestResult(document=existing, cached=True)

        dest = root / file_hash
        if not dest.exists():
            os.replace(tmp_path, dest)
    finally:
        tmp_path.unlink(missing_ok=True)

    doc = Document(
        filename=_sanitise_filename(filename),
        file_hash=file_hash,
        file_type=detected if detected not in ("zip_maybe", "ole_maybe") else "sheet",
        size_bytes=size,
        status="queued",
    )
    try:
        # savepoint: a concurrent upload of the same file may win the unique index
        with session.begin_nested():
            session.add(doc)
            session.flush()
    except IntegrityError:
        winner = _find_by_hash(session, file_hash)
        if winner is None:
            raise
        return IngestResult(document=winner, cached=True)

    return IngestResult(document=doc, cached=False)
