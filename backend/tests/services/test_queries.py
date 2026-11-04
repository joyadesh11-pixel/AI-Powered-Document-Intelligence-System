"""Tests for services/queries.py (read-only views behind the GET endpoints)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.db.models import (
    Correction, Document, DocumentPage, Invoice, LineItem, ProcessingEvent, ValidationIssue,
)
from app.services import queries
from app.services.errors import NotFound, ServiceError


def _doc(session, name: str, status: str = "validated_ok", age_min: int = 0,
         n_invoices: int = 1, n_issues: int = 0) -> Document:
    created = datetime.now(timezone.utc) - timedelta(minutes=age_min)
    doc = Document(filename=name, file_hash=uuid.uuid4().hex * 2, file_type="pdf",
                   size_bytes=1, status=status, created_at=created)
    session.add(doc)
    session.flush()
    for i in range(n_invoices):
        inv = Invoice(document_id=doc.id, unit_index=i, invoice_number=f"{name}-{i}",
                      status=status, tier="parser")
        session.add(inv)
        session.flush()
        for j in range(n_issues):
            session.add(ValidationIssue(document_id=doc.id, invoice_id=inv.id, code="X",
                                        severity="error" if j == 0 else "warning", message="m"))
    session.commit()
    return doc


def test_list_counts_without_n_plus_one(session):
    _doc(session, "a.pdf", n_invoices=2, n_issues=3)
    _doc(session, "b.pdf", n_invoices=0)
    listing = queries.list_documents(session, sort="filename:asc")
    rows = {r.document.filename: r for r in listing.items}
    assert listing.total == 2
    assert (rows["a.pdf"].invoice_count, rows["a.pdf"].issue_count, rows["a.pdf"].error_count) == (2, 6, 2)
    assert (rows["b.pdf"].invoice_count, rows["b.pdf"].issue_count) == (0, 0)


def test_list_filters_sorts_and_paginates(session):
    _doc(session, "old.pdf", status="needs_review", age_min=30)
    _doc(session, "mid.pdf", status="needs_review", age_min=20)
    _doc(session, "new.pdf", status="validated_ok", age_min=10)

    page = queries.list_documents(session, status="needs_review", sort="created_at:desc", limit=1)
    assert page.total == 2
    assert [r.document.filename for r in page.items] == ["mid.pdf"]

    page2 = queries.list_documents(session, status="needs_review", sort="created_at:desc", limit=1, offset=1)
    assert [r.document.filename for r in page2.items] == ["old.pdf"]

    assert [r.document.filename for r in queries.list_documents(session, q="NEW").items] == ["new.pdf"]


@pytest.mark.parametrize("sort", ["created_at", "password:asc", "created_at:sideways", "id; drop"])
def test_bad_sort_rejected(session, sort):
    with pytest.raises(ServiceError) as exc_info:
        queries.list_documents(session, sort=sort)
    assert exc_info.value.code == "INVALID_SORT"


def test_q_wildcards_literal(session):
    _doc(session, "plain.pdf")
    assert queries.list_documents(session, q="%").items == []


def test_get_document_detail(session):
    doc = _doc(session, "d.pdf", n_invoices=1, n_issues=1)
    inv = session.query(Invoice).filter_by(document_id=doc.id).one()
    session.add_all([
        LineItem(invoice_id=inv.id, position=2, description="b", amount=Decimal("2")),
        LineItem(invoice_id=inv.id, position=1, description="a", amount=Decimal("1")),
        ProcessingEvent(document_id=doc.id, attempt=2, node="intake"),
        ProcessingEvent(document_id=doc.id, attempt=1, node="intake"),
        ProcessingEvent(document_id=doc.id, attempt=1, node="finalize"),
        ValidationIssue(document_id=doc.id, invoice_id=None, code="SEGMENT_FALLBACK",
                        severity="info", message="doc-level"),
    ])
    session.commit()

    detail = queries.get_document(session, doc.id)
    assert detail.document.id == doc.id
    assert [(e.attempt, e.node) for e in detail.events] == [(1, "intake"), (1, "finalize"), (2, "intake")]
    assert [li.position for li in detail.invoices[0].line_items] == [1, 2]
    assert [i.code for i in detail.document_issues] == ["SEGMENT_FALLBACK"]
    assert [i.code for i in detail.invoices[0].issues] == ["X"]


def test_get_document_missing(session):
    with pytest.raises(NotFound):
        queries.get_document(session, uuid.uuid4())


def test_get_pages_in_order(session):
    doc = _doc(session, "p.pdf")
    session.add_all([DocumentPage(document_id=doc.id, page_no=2, text="two", source="ocr", ocr_conf=0.9),
                     DocumentPage(document_id=doc.id, page_no=1, text="one", source="digital")])
    session.commit()
    assert [p.page_no for p in queries.get_pages(session, doc.id)] == [1, 2]
    with pytest.raises(NotFound):
        queries.get_pages(session, uuid.uuid4())


def test_get_invoice_with_corrections(session):
    doc = _doc(session, "i.pdf")
    inv = session.query(Invoice).filter_by(document_id=doc.id).one()
    session.add(Correction(invoice_id=inv.id, field="total", old_value="1", new_value="2"))
    session.commit()
    got = queries.get_invoice(session, inv.id)
    assert got.id == inv.id
    assert [c.field for c in got.corrections] == ["total"]
    with pytest.raises(NotFound):
        queries.get_invoice(session, uuid.uuid4())
