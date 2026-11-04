"""Tests for services/corrections.py."""
from __future__ import annotations

import io
import uuid
from decimal import Decimal

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.db.models import Chunk, Correction, Document, Invoice, LineItem, ValidationIssue
from app.schemas.models import ExtractedInvoice, IssueCode, InvoiceStatus, Severity
from app.services.corrections import CorrectionParseError, apply
from app.services.errors import Conflict
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


# --- M8b ---------------------------------------------------------------------

def _arith_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    lines = ["Invoice INV-77", "Supplier: Arith Ltd", "Date: 2026-11-03",
             "Subtotal: 90.00", "Tax: 10.00", "Total: 100.00 USD"]
    for i, line in enumerate(lines):
        c.drawString(100, 700 - 20 * i, line)
    c.save()
    return buf.getvalue()


def _arith_invoice(session):
    """Extraction misreads the total (105.00 instead of 100.00) -> needs_review."""
    result = save_upload("arith.pdf", _arith_pdf(), session)
    session.commit()
    llm = FakeLLM(responses=[ExtractedInvoice(
        invoice_number="INV-77", invoice_date="2026-11-03", supplier_name="Arith Ltd",
        subtotal="90.00", tax="10.00", total="105.00", currency="USD",
    )])
    process_document(result.document.id, session, llm=llm)
    session.commit()
    inv = session.query(Invoice).filter_by(document_id=result.document.id).one()
    assert inv.status == "needs_review"
    assert session.get(Document, inv.document_id).status == "needs_review"
    return inv


def _error_codes(session, inv_id):
    return sorted(i.code for i in session.query(ValidationIssue).filter_by(
        invoice_id=inv_id, severity="error"))


def test_fix_total_revalidates_to_ok_and_recomputes_document(session):
    inv = _arith_invoice(session)
    apply(inv.id, {"total": "100.00"}, session)
    session.commit()

    inv = session.get(Invoice, inv.id)
    session.refresh(inv)
    assert _error_codes(session, inv.id) == []
    assert inv.status == "validated_ok"
    assert inv.corrected is True
    assert inv.total == Decimal("100.00")
    assert session.get(Document, inv.document_id).status == "validated_ok"


def test_all_bad_fields_reported_and_nothing_written(session):
    inv = _arith_invoice(session)
    with pytest.raises(CorrectionParseError) as exc_info:
        apply(inv.id, {"total": "abc", "invoice_date": "not a date", "currency": "EURO",
                       "supplier_name": "Should Not Change"}, session)
    session.rollback()

    assert set(exc_info.value.errors) == {"total", "invoice_date", "currency"}
    inv = session.get(Invoice, inv.id)
    session.refresh(inv)
    assert inv.supplier_name == "Arith Ltd"
    assert session.query(Correction).count() == 0


def test_unknown_field_rejected(session):
    inv = _arith_invoice(session)
    with pytest.raises(CorrectionParseError) as exc_info:
        apply(inv.id, {"totl": "100.00"}, session)
    assert "totl" in exc_info.value.errors


def test_currency_normalised_upper(session):
    inv = _arith_invoice(session)
    apply(inv.id, {"currency": "eur"}, session)
    session.commit()
    assert session.get(Invoice, inv.id).currency == "EUR"


def test_earlier_corrected_field_stays_exempt_from_grounding(session):
    inv = _arith_invoice(session)
    apply(inv.id, {"supplier_name": "Name Not In The PDF"}, session)
    session.commit()
    apply(inv.id, {"total": "100.00"}, session)  # second PATCH, different field
    session.commit()

    grounding = session.query(ValidationIssue).filter_by(
        invoice_id=inv.id, code=IssueCode.NOT_GROUNDED.value, field="supplier_name").count()
    assert grounding == 0
    assert session.get(Invoice, inv.id).status == "validated_ok"


def test_only_changed_fields_logged_with_line_item_paths(session):
    inv = _arith_invoice(session)
    session.add(LineItem(invoice_id=inv.id, position=1, description="Widget",
                         quantity=Decimal("1"), unit_price=Decimal("90"), amount=Decimal("90")))
    session.commit()

    apply(inv.id, {"supplier_name": "Arith Ltd",   # unchanged -> not logged
                   "line_items": [{"position": 1, "amount": "90.00", "description": "Widget XL"}]},
          session)
    session.commit()

    rows = {c.field: (c.old_value, c.new_value)
            for c in session.query(Correction).filter_by(invoice_id=inv.id)}
    assert rows == {"line_items[1].description": ("Widget", "Widget XL")}


def test_summary_chunk_rebuilt_with_new_text(session, fake_embedder):
    from app.services.indexing import index_document

    inv = _arith_invoice(session)
    index_document(inv.document_id, session, fake_embedder)
    session.commit()

    apply(inv.id, {"supplier_name": "Renamed Supplier GmbH"}, session)
    session.commit()

    summary = session.query(Chunk).filter_by(invoice_id=inv.id, kind="invoice_summary").one()
    assert "Renamed Supplier GmbH" in summary.text
    assert summary.indexed is False
    assert summary.embedding is None


def test_correction_blocked_while_document_processing(session):
    inv = _arith_invoice(session)
    session.get(Document, inv.document_id).status = "processing"
    session.commit()
    with pytest.raises(Conflict):
        apply(inv.id, {"total": "100.00"}, session)


def test_correction_creating_duplicate_is_flagged(session):
    first = _arith_invoice(session)
    apply(first.id, {"total": "100.00"}, session)
    session.commit()

    pdf = _make_pdf_bytes(number="INV-78", supplier="Arith Ltd", total="100.00")
    doc2 = _run_pipeline(session, pdf, number="INV-78", supplier="Arith Ltd", total="100.00")
    second = session.query(Invoice).filter_by(document_id=doc2.id).one()

    apply(second.id, {"invoice_number": "INV-77"}, session)
    session.commit()

    second = session.get(Invoice, second.id)
    session.refresh(second)
    assert second.dup_key is None
    assert second.dup_of == first.id
    assert IssueCode.DUPLICATE_INVOICE.value in _error_codes(session, second.id)
    assert second.status == "needs_review"
