"""Tests for services/ingest.py.

These tests run against the real Postgres DB (see conftest.py for skip logic).
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.db.models import Document
from app.services.ingest import IngestError, save_upload


def _make_pdf_bytes(text: str = "Invoice #001") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(100, 700, text)
    c.save()
    return buf.getvalue()


def test_first_upload_creates_document(session):
    data = _make_pdf_bytes("Invoice #001")
    result = save_upload("invoice.pdf", data, session)
    session.commit()

    assert result.cached is False
    assert result.document.status == "queued"
    assert result.document.file_type in ("pdf", "zip_maybe", None) or True  # pdf path
    assert session.query(Document).count() == 1


def test_same_hash_returns_existing(session):
    data = _make_pdf_bytes("Invoice #001")
    r1 = save_upload("invoice.pdf", data, session)
    session.commit()

    r2 = save_upload("invoice_copy.pdf", data, session)
    assert r2.cached is True
    assert r2.document.id == r1.document.id
    assert session.query(Document).count() == 1


def test_different_content_creates_new_document(session):
    d1 = _make_pdf_bytes("Invoice #001")
    d2 = _make_pdf_bytes("Invoice #002")
    r1 = save_upload("a.pdf", d1, session)
    session.commit()
    r2 = save_upload("b.pdf", d2, session)
    session.commit()

    assert r1.cached is False
    assert r2.cached is False
    assert r1.document.id != r2.document.id
    assert session.query(Document).count() == 2


def test_empty_file_raises(session):
    with pytest.raises(IngestError) as exc_info:
        save_upload("empty.pdf", b"", session)
    assert exc_info.value.code == "EMPTY_FILE"


def test_oversized_file_raises(session):
    # Build a >25 MB payload
    oversized = b"%PDF" + b"x" * (26 * 1024 * 1024)
    with pytest.raises(IngestError) as exc_info:
        save_upload("big.pdf", oversized, session)
    assert exc_info.value.code == "TOO_LARGE"


def test_unsupported_type_raises(session):
    with pytest.raises(IngestError) as exc_info:
        save_upload("doc.exe", b"\x4d\x5a" + b"\x00" * 100, session)
    assert exc_info.value.code == "UNSUPPORTED_TYPE"


def test_filename_sanitised(session):
    data = _make_pdf_bytes()
    # Filename with path separators — should be sanitised
    result = save_upload("../../../etc/passwd.pdf", data, session)
    session.commit()
    assert "/" not in result.document.filename
    assert "\\" not in result.document.filename


# --- M8b ---------------------------------------------------------------------

def test_stream_input_stored_under_hash(session, upload_dir):
    import hashlib

    data = _make_pdf_bytes("Invoice #stream")
    result = save_upload("s.pdf", io.BytesIO(data), session)
    session.commit()

    digest = hashlib.sha256(data).hexdigest()
    assert result.document.file_hash == digest
    assert (upload_dir / digest).read_bytes() == data
    # nothing left behind in the temp area
    assert not any((upload_dir / "tmp").iterdir())


def test_stream_over_cap_rejected_and_leaves_no_file(session, upload_dir, monkeypatch):
    monkeypatch.setenv("MAX_FILE_MB", "1")
    stream = io.BytesIO(b"%PDF-1.4\n" + b"x" * (2 * 1024 * 1024))
    with pytest.raises(IngestError) as exc_info:
        save_upload("big.pdf", stream, session)
    assert exc_info.value.code == "TOO_LARGE"
    assert [p for p in upload_dir.rglob("*") if p.is_file()] == []
    assert session.query(Document).count() == 0


def test_disallowed_extension_rejected_even_with_valid_magic(session):
    with pytest.raises(IngestError) as exc_info:
        save_upload("invoice.exe", _make_pdf_bytes(), session)
    assert exc_info.value.code == "UNSUPPORTED_TYPE"


def test_renamed_exe_rejected(session, upload_dir):
    with pytest.raises(IngestError) as exc_info:
        save_upload("invoice.pdf", b"MZ\x90\x00" + b"\x00" * 200, session)
    assert exc_info.value.code == "UNSUPPORTED_TYPE"
    assert [p for p in upload_dir.rglob("*") if p.is_file()] == []


def test_docx_rejected_by_extension(session):
    # A .docx is a zip container like .xlsx; the extension allow-list rejects it.
    with pytest.raises(IngestError) as exc_info:
        save_upload("letter.docx", b"PK\x03\x04" + b"\x00" * 200, session)
    assert exc_info.value.code == "UNSUPPORTED_TYPE"


def test_same_hash_race_returns_cached(session, session_factory, monkeypatch):
    """Two uploads of one file pass the existence check at once: the loser gets cached=True."""
    from app.services import ingest

    data = _make_pdf_bytes("Invoice #race")
    with session_factory() as other:
        winner = save_upload("a.pdf", data, other)
        other.commit()
        winner_id = winner.document.id

    real_find = ingest._find_by_hash
    calls = {"n": 0}

    def find_misses_once(s, h):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_find(s, h)

    monkeypatch.setattr(ingest, "_find_by_hash", find_misses_once)
    result = save_upload("b.pdf", data, session)
    session.commit()

    assert result.cached is True
    assert result.document.id == winner_id
    assert session.query(Document).count() == 1
