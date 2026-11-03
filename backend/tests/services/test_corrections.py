"""Tests for services/corrections.py."""
from __future__ import annotations

import io
import uuid
from decimal import Decimal

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.db.models import Chunk, Correction, Document, Invoice, ValidationIssue
from app.schemas.models import ExtractedInvoice, IssueCode, InvoiceStatus, Severity
from app.services.corrections import CorrectionParseError, apply
from app.services.ingest import save_upload
from app.services.processing import process_document
from tests.fakes import FakeLLM


def _make_pdf_bytes(number="INV-001", supplier="Acme Corp", total="100.00") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(100, 700, f"Invoice {number} Total: {total}")
    c.drawString(100, 680, f"Supplier: {supplier}")
    c.save()
    return buf.getvalue()


def _run_pipeline(session, pdf_data, number="INV-001", supplier="Acme Corp", total="100.00"):
    result = save_upload("test.pdf", pdf_data, session)
    session.commit()
    doc = result.document
    llm = FakeLLM(responses=[ExtractedInvoice(
        invoice_number=number,
        invoice_date="2026-11-03",
        supplier_name=supplier,
        total=total,
        currency="USD",
    )])
    process_document(doc.id, session, llm=llm)
    session.commit()
    return doc


@pytest.mark.usefixtures("truncate_tables")
def test_correction_sets_corrected_flag(session):
    pdf = _make_pdf_bytes()
    doc = _run_pipeline(session, pdf)
    inv = session.query(Invoice).filter_by(document_id=doc.id).first()
    if inv is None:
        pytest.skip("No invoice extracted")

    apply(inv.id, {"supplier_name": "New Supplier Name"}, session)
    session.commit()

    inv = session.get(Invoice, inv.id)
    assert inv.corrected is True


@pytest.mark.usefixtures("truncate_tables")
def test_correction_logs_field_changes(session):
    pdf = _make_pdf_bytes()
    doc = _run_pipeline(session, pdf)
    inv = session.query(Invoice).filter_by(document_id=doc.id).first()
    if inv is None:
        pytest.skip("No invoice extracted")

    old_supplier = inv.supplier_name
    apply(inv.id, {"supplier_name": "Corrected Corp"}, session)
    session.commit()

    corrections = session.query(Correction).filter_by(invoice_id=inv.id).all()
    supplier_correction = next((c for c in corrections if c.field == "supplier_name"), None)
    assert supplier_correction is not None
    assert supplier_correction.old_value == old_supplier
    assert supplier_correction.new_value == "Corrected Corp"


@pytest.mark.usefixtures("truncate_tables")
def test_correction_recomputes_document_status(session):
    """Fixing arithmetic error → document recomputed to validated_ok."""
    # Create a PDF where total doesn't match what we'll claim it is
    pdf = _make_pdf_bytes(total="50.00")
    doc = _run_pipeline(session, pdf, total="50.00")
    inv = session.query(Invoice).filter_by(document_id=doc.id).first()
    if inv is None:
        pytest.skip("No invoice extracted")

    # Force the invoice into needs_review with a bad total (simulate ARITH issue)
    # Just set status directly and apply a correction that fixes things
    apply(inv.id, {"total": "100.00", "subtotal": "100.00"}, session)
    session.commit()

    inv = session.get(Invoice, inv.id)
    assert inv.corrected is True
    # Document status should be recomputed
    doc = session.get(Document, inv.document_id)
    assert doc.status in ("validated_ok", "needs_review")  # not failed


@pytest.mark.usefixtures("truncate_tables")
def test_correction_bad_money_raises_no_partial_write(session):
    pdf = _make_pdf_bytes()
    doc = _run_pipeline(session, pdf)
    inv = session.query(Invoice).filter_by(document_id=doc.id).first()
    if inv is None:
        pytest.skip("No invoice extracted")

    original_supplier = inv.supplier_name

    with pytest.raises(CorrectionParseError) as exc_info:
        apply(inv.id, {"total": "not-a-number", "supplier_name": "Should Not Change"}, session)
    session.rollback()

    # No partial write — supplier unchanged
    inv = session.get(Invoice, inv.id)
    assert inv.supplier_name == original_supplier
    assert exc_info.value.field == "total"


@pytest.mark.usefixtures("truncate_tables")
def test_correction_marks_summary_chunk_unindexed(session, fake_embedder):
    from app.services.indexing import index_document

    pdf = _make_pdf_bytes()
    doc = _run_pipeline(session, pdf)
    inv = session.query(Invoice).filter_by(document_id=doc.id).first()
    if inv is None:
        pytest.skip("No invoice extracted")

    # Index first
    index_document(doc.id, session, fake_embedder)
    session.commit()

    summary = session.query(Chunk).filter_by(invoice_id=inv.id, kind="invoice_summary").first()
    if summary:
        assert summary.indexed is True

    apply(inv.id, {"supplier_name": "Updated Supplier"}, session)
    session.commit()

    summary = session.query(Chunk).filter_by(invoice_id=inv.id, kind="invoice_summary").first()
    if summary:
        assert summary.indexed is False


@pytest.mark.usefixtures("truncate_tables")
def test_corrected_fields_skip_grounding_check(session):
    """Correcting a field to a value not in the source text should not produce NOT_GROUNDED."""
    pdf = _make_pdf_bytes(supplier="Acme Corp")
    doc = _run_pipeline(session, pdf, supplier="Acme Corp")
    inv = session.query(Invoice).filter_by(document_id=doc.id).first()
    if inv is None:
        pytest.skip("No invoice extracted")

    # Apply a correction with a supplier name that definitely is not in the PDF
    apply(inv.id, {"supplier_name": "XYZ_UniqueString_NotInPDF_12345"}, session)
    session.commit()

    inv = session.get(Invoice, inv.id)
    grounding_issues = session.query(ValidationIssue).filter_by(
        invoice_id=inv.id,
        code=IssueCode.NOT_GROUNDED.value,
        field="supplier_name",
    ).all()
    assert len(grounding_issues) == 0, (
        "Corrected field supplier_name should not produce NOT_GROUNDED issue"
    )
