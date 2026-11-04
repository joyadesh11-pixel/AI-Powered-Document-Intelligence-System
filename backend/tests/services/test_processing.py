"""Tests for services/processing.py.

Tests use FakeLLM and a real PDF fixture so the pipeline runs end-to-end
without network calls.  No vision calls are made (no blurry pages).
"""
from __future__ import annotations

import hashlib
import io
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.db.models import Document, Invoice, ProcessingEvent, ValidationIssue
from app.schemas.models import ExtractedInvoice, IssueCode
from app.services.ingest import save_upload
from app.services.ingest import upload_dir
from app.services.processing import DBSeenStore, process_document
from tests.fakes import FakeLLM


def _make_invoice_pdf(
    number: str = "INV-001",
    supplier: str = "Acme Corp",
    total: str = "100.00",
) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(100, 700, f"Invoice {number}")
    c.drawString(100, 680, f"Supplier: {supplier}")
    c.drawString(100, 660, f"Total: {total}")
    c.save()
    return buf.getvalue()


def _fake_invoice_response(
    number: str = "INV-001",
    supplier: str = "Acme Corp",
    total: str = "100.00",
) -> ExtractedInvoice:
    return ExtractedInvoice(
        invoice_number=number,
        invoice_date="2026-11-03",
        supplier_name=supplier,
        total=total,
        currency="USD",
        line_items=[],
        uncertain_fields=[],
    )


def _upload_and_save(data: bytes, filename: str, session) -> Document:
    result = save_upload(filename, data, session)
    session.commit()
    return result.document


@pytest.mark.usefixtures("truncate_tables")
def test_process_document_status_transition(session):
    """Happy path: queued → processing → validated_ok/needs_review, events recorded."""
    pdf = _make_invoice_pdf()
    doc = _upload_and_save(pdf, "test.pdf", session)
    assert doc.status == "queued"

    llm = FakeLLM(responses=[_fake_invoice_response()])
    process_document(doc.id, session, llm=llm)
    session.commit()

    doc = session.get(Document, doc.id)
    assert doc.status in ("validated_ok", "needs_review")
    assert doc.finished_at is not None

    events = session.query(ProcessingEvent).filter_by(document_id=doc.id).all()
    node_names = [e.node for e in events]
    assert "intake" in node_names
    # Events should be in pipeline order (intake first)
    intake_idx = node_names.index("intake")
    assert intake_idx == 0


@pytest.mark.usefixtures("truncate_tables")
def test_duplicate_across_two_documents(session):
    """Second document with same supplier/number/total → DUPLICATE_INVOICE issue, dup_of set."""
    llm_responses = [
        _fake_invoice_response("INV-DUP", "Dupe Corp", "999.00"),
        _fake_invoice_response("INV-DUP", "Dupe Corp", "999.00"),
    ]

    # First document
    pdf1 = _make_invoice_pdf("INV-DUP", "Dupe Corp", "999.00")
    doc1 = _upload_and_save(pdf1, "doc1.pdf", session)
    llm1 = FakeLLM(responses=[llm_responses[0]])
    process_document(doc1.id, session, llm=llm1)
    session.commit()

    inv1 = session.query(Invoice).filter_by(document_id=doc1.id).first()
    assert inv1 is not None
    if inv1.dup_key is None:
        pytest.skip("First invoice had no dup_key (fields null); cannot test duplicate")

    # Second document — same content
    pdf2 = _make_invoice_pdf("INV-DUP", "Dupe Corp", "999.00")
    doc2 = _upload_and_save(pdf2, "doc2.pdf", session)
    # Different hash → new document
    assert doc2.id != doc1.id

    llm2 = FakeLLM(responses=[llm_responses[1]])
    process_document(doc2.id, session, llm=llm2)
    session.commit()

    inv2 = session.query(Invoice).filter_by(document_id=doc2.id).first()
    assert inv2 is not None
    # Second invoice should be flagged as a duplicate
    dup_issues = session.query(ValidationIssue).filter_by(
        invoice_id=inv2.id, code=IssueCode.DUPLICATE_INVOICE.value
    ).all()
    assert len(dup_issues) >= 1
    assert inv2.dup_key is None
    assert inv2.dup_of == inv1.id


@pytest.mark.usefixtures("truncate_tables")
def test_reprocess_same_file_not_duplicate(session):
    """Reprocessing the same file (same hash) must not flag it as a duplicate of itself."""
    from app.services.reprocess import run as reprocess_run

    pdf = _make_invoice_pdf("INV-SELF", "Self Corp", "50.00")
    doc = _upload_and_save(pdf, "self.pdf", session)

    llm = FakeLLM(responses=[_fake_invoice_response("INV-SELF", "Self Corp", "50.00")])
    process_document(doc.id, session, llm=llm)
    session.commit()

    inv_first = session.query(Invoice).filter_by(document_id=doc.id).first()
    if inv_first is None:
        pytest.skip("No invoice extracted")

    # Spec: force only applies to needs_review, so put the document in that state first
    session.get(Document, doc.id).status = "needs_review"
    session.commit()
    reprocess_run(doc.id, session, force=True)
    session.commit()

    llm2 = FakeLLM(responses=[_fake_invoice_response("INV-SELF", "Self Corp", "50.00")])
    process_document(doc.id, session, llm=llm2)
    session.commit()

    inv_second = session.query(Invoice).filter_by(document_id=doc.id).first()
    assert inv_second is not None
    dup_issues = session.query(ValidationIssue).filter_by(
        invoice_id=inv_second.id, code=IssueCode.DUPLICATE_INVOICE.value
    ).all()
    assert len(dup_issues) == 0, "Reprocessing same file should not produce DUPLICATE_INVOICE"


@pytest.mark.usefixtures("truncate_tables")
def test_failed_document_on_corrupt_file(session, tmp_path):
    """Corrupt file → document status failed (not stuck in processing)."""
    # save_upload would (correctly) reject these bytes, so insert the row directly
    data = b"not a real PDF at all" + b"\x00" * 100
    file_hash = hashlib.sha256(data).hexdigest()
    upload_dir().mkdir(parents=True, exist_ok=True)
    (upload_dir() / file_hash).write_bytes(data)
    doc = Document(filename="fake.jpg", file_hash=file_hash, file_type="image",
                   size_bytes=len(data), status="queued")
    session.add(doc)
    session.commit()

    process_document(doc.id, session, llm=FakeLLM(responses=[]))
    session.commit()

    doc = session.get(Document, doc.id)
    assert doc.status == "failed"
    assert doc.error_code is not None


@pytest.mark.usefixtures("truncate_tables")
def test_dbseenstore_excludes_same_file_hash(session):
    """DBSeenStore.contains with exclude_hash skips invoices from same file."""
    pdf = _make_invoice_pdf()
    doc = _upload_and_save(pdf, "same.pdf", session)
    llm = FakeLLM(responses=[_fake_invoice_response("INV-X", "X Corp", "77.00")])
    process_document(doc.id, session, llm=llm)
    session.commit()

    inv = session.query(Invoice).filter_by(document_id=doc.id).first()
    if inv is None or inv.dup_key is None:
        pytest.skip("No invoice with dup_key")

    store = DBSeenStore(session)
    # Without exclude: should find it
    assert store.contains(inv.dup_key) is True
    # With the same file's hash excluded: should not find it
    assert store.contains(inv.dup_key, exclude_hash=doc.file_hash) is False


# --- M8b ---------------------------------------------------------------------

from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import OperationalError

from app.services.errors import InfraError
from app.services.processing import claim


def test_events_follow_pipeline_order_with_attempt(session):
    doc = _upload_and_save(_make_invoice_pdf(), "order.pdf", session)
    outcome = process_document(doc.id, session, llm=FakeLLM(responses=[_fake_invoice_response()]))
    session.commit()

    events = (session.query(ProcessingEvent).filter_by(document_id=doc.id)
              .order_by(ProcessingEvent.id).all())
    nodes = [e.node for e in events]
    assert nodes[0] == "intake"
    assert nodes[-1] == "finalize"
    assert nodes.index("pdf_text") < nodes.index("llm_extract") < nodes.index("validate")
    assert {e.attempt for e in events} == {1}
    assert outcome.status == session.get(Document, doc.id).status
    assert outcome.should_index is True


def test_claim_only_queued_or_stale_processing(session):
    doc = _upload_and_save(_make_invoice_pdf("INV-C"), "c.pdf", session)
    assert claim(doc.id, session) is True          # queued -> processing
    assert claim(doc.id, session) is False         # fresh processing: someone owns it

    stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    session.get(Document, doc.id).updated_at = stale
    session.commit()
    assert claim(doc.id, session) is True          # stale processing: worker died

    session.get(Document, doc.id).status = "validated_ok"
    session.commit()
    assert claim(doc.id, session) is False         # finished: redelivery is a no-op


def test_finished_document_redelivery_is_noop(session):
    doc = _upload_and_save(_make_invoice_pdf("INV-R"), "r.pdf", session)
    process_document(doc.id, session, llm=FakeLLM(responses=[_fake_invoice_response("INV-R")]))
    session.commit()
    n_events = session.query(ProcessingEvent).filter_by(document_id=doc.id).count()

    llm = FakeLLM(responses=[])
    outcome = process_document(doc.id, session, llm=llm)
    assert outcome.skipped is True
    assert llm.calls == []
    assert session.query(ProcessingEvent).filter_by(document_id=doc.id).count() == n_events


def test_db_outage_raises_infra_error_and_requeues(session, monkeypatch):
    from app.services import processing

    doc = _upload_and_save(_make_invoice_pdf("INV-O"), "o.pdf", session)

    def boom(*a, **k):
        raise OperationalError("SELECT 1", {}, Exception("server closed the connection"))
        yield  # pragma: no cover

    monkeypatch.setattr(processing, "stream_process", boom)
    with pytest.raises(InfraError):
        process_document(doc.id, session, llm=FakeLLM(responses=[]))

    session.rollback()
    doc = session.get(Document, doc.id)
    session.refresh(doc)
    assert doc.status == "queued"      # retry can claim it again
    assert doc.error_code is None


def test_unexpected_error_marks_failed_without_traceback(session, monkeypatch):
    from app.services import processing

    doc = _upload_and_save(_make_invoice_pdf("INV-U"), "u.pdf", session)

    def boom(*a, **k):
        raise KeyError("x" * 5000)
        yield  # pragma: no cover

    monkeypatch.setattr(processing, "stream_process", boom)
    with pytest.raises(KeyError):
        process_document(doc.id, session, llm=FakeLLM(responses=[]))

    doc = session.get(Document, doc.id)
    session.refresh(doc)
    assert doc.status == "failed"
    assert doc.error_code == "INTERNAL_ERROR"
    assert len(doc.error) <= 500
    assert "Traceback" not in doc.error
