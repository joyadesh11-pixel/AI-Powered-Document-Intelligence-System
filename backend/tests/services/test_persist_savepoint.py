"""A dup_key collision inside _persist_result must not discard the rest of the result."""
from __future__ import annotations

import pytest

from app.db.models import Document, DocumentPage, Invoice, ValidationIssue
from app.schemas.models import PipelineResult
from app.services.processing import DBMappingCache, _persist_result


def _result(invoices: list[dict]) -> PipelineResult:
    return PipelineResult.model_validate({
        "status": "validated_ok", "file_hash": "h" * 64, "file_type": "pdf",
        "size_bytes": 10, "page_count": 1, "invoices": invoices, "warnings": [],
        "page_text": {"1": "Invoice text"}, "page_source": {"1": "text"},
        "ocr_conf": {}, "timings": {}, "history": [], "llm_usage": {},
        "pipeline_version": "test",
    })


@pytest.mark.usefixtures("truncate_tables")
def test_dup_key_collision_keeps_pages_and_both_invoices(session):
    inv = {
        "unit_id": 0, "invoice_number": "INV-1", "supplier_name": "Acme",
        "total": "10.00", "currency": "USD", "status": "validated_ok",
        "tier": "parser", "line_items": [], "issues": [],
    }
    first = Document(filename="a.pdf", file_hash="a" * 64, file_type="pdf", size_bytes=1, status="queued")
    second = Document(filename="b.pdf", file_hash="b" * 64, file_type="pdf", size_bytes=1, status="queued")
    session.add_all([first, second])
    session.commit()

    cache = DBMappingCache(session)
    _persist_result(first.id, _result([{**inv}]), session, cache)
    session.commit()
    # Same dup_key but no DUPLICATE_INVOICE issue: takes the insert path and collides
    _persist_result(second.id, _result([{**inv, "unit_id": 0}]), session, cache)
    session.commit()

    assert session.query(Invoice).filter_by(document_id=second.id).count() == 1
    assert session.query(DocumentPage).filter_by(document_id=second.id).count() == 1


@pytest.mark.usefixtures("truncate_tables")
def test_persist_time_race_flags_duplicate(session):
    """Validate saw no duplicate, but another upload committed the same key first:
    the loser is stored as a flagged duplicate and the document needs review."""
    inv = {
        "unit_id": 0, "invoice_number": "INV-9", "supplier_name": "Race Co",
        "total": "10.00", "currency": "USD", "status": "validated_ok",
        "tier": "parser", "line_items": [], "issues": [],
    }
    first = Document(filename="a.pdf", file_hash="c" * 64, file_type="pdf", size_bytes=1, status="queued")
    second = Document(filename="b.pdf", file_hash="d" * 64, file_type="pdf", size_bytes=1, status="queued")
    session.add_all([first, second])
    session.commit()

    cache = DBMappingCache(session)
    _persist_result(first.id, _result([{**inv}]), session, cache)
    session.commit()
    _persist_result(second.id, _result([{**inv}]), session, cache)
    session.commit()

    winner = session.query(Invoice).filter_by(document_id=first.id).one()
    loser = session.query(Invoice).filter_by(document_id=second.id).one()
    assert loser.dup_key is None
    assert loser.dup_of == winner.id
    assert loser.status == "needs_review"
    codes = [i.code for i in session.query(ValidationIssue).filter_by(invoice_id=loser.id)]
    assert codes == ["DUPLICATE_INVOICE"]
    assert session.get(Document, second.id).status == "needs_review"
    assert session.get(Document, first.id).status == "validated_ok"


@pytest.mark.usefixtures("truncate_tables")
def test_doc_status_is_worst_of_pipeline_and_invoices(session):
    """A needs_review invoice drags the document down even if the result said validated_ok."""
    inv = {
        "unit_id": 0, "invoice_number": "INV-5", "supplier_name": "W Co",
        "total": "10.00", "currency": "USD", "status": "needs_review",
        "tier": "parser", "line_items": [], "issues": [],
    }
    doc = Document(filename="w.pdf", file_hash="e" * 64, file_type="pdf", size_bytes=1, status="queued")
    session.add(doc)
    session.commit()
    _persist_result(doc.id, _result([inv]), session, DBMappingCache(session))
    session.commit()
    assert session.get(Document, doc.id).status == "needs_review"
