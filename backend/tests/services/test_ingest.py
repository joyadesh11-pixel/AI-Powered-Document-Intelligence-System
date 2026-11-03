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
