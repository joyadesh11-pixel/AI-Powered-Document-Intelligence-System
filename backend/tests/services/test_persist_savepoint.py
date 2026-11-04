"""A dup_key collision inside _persist_result must not discard the rest of the result."""
from __future__ import annotations

import pytest

from app.db.models import Document, DocumentPage, Invoice
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
    assert session.get(Document, second.id).status == "validated_ok"
