"""Search behaviour added in M8b: real RRF fusion, filters on page chunks,
ILIKE escaping, typed filters and the degraded hybrid fallback.

Rows are inserted directly (no pipeline run) so each test controls exactly
which chunks exist.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.db.models import Chunk, Document, DocumentPage, Invoice
from app.services.errors import ServiceError
from app.services.indexing import index_document
from app.services.search import query
from tests.fakes import FailingEmbedder


def _doc(session, embedder, *, page_text: str, supplier: str, number: str,
         total: str = "100.00", currency: str = "USD", status: str = "validated_ok",
         inv_date: str = "2026-11-03", index: bool = True) -> Document:
    doc = Document(filename=f"{number}.pdf", file_hash=uuid.uuid4().hex * 2, file_type="pdf",
                   size_bytes=1, status=status)
    session.add(doc)
    session.flush()
    session.add(DocumentPage(document_id=doc.id, page_no=1, text=page_text, source="digital"))
    session.add(Invoice(document_id=doc.id, unit_index=0, invoice_number=number,
                        supplier_name=supplier, total=Decimal(total), currency=currency,
                        invoice_date=inv_date, status=status, tier="parser"))
    session.commit()
    if index:
        index_document(doc.id, session, embedder)
        session.commit()
    return doc


def _keys(page):
    return {(r.document_id, r.invoice_id) for r in page.items}


def test_returns_page_with_total(session, fake_embedder):
    _doc(session, fake_embedder, page_text="widgets shipped", supplier="Acme", number="A-1")
    page = query("widgets", mode="keyword", session=session)
    assert page.total == len(page.items) >= 1
    assert page.degraded is False


def test_keyword_partial_supplier_via_ilike(session, fake_embedder):
    _doc(session, fake_embedder, page_text="nothing relevant", supplier="ZapCorp Industries", number="Z-1")
    page = query("zapc", mode="keyword", session=session)
    assert [r.supplier_name for r in page.items if r.invoice_id] == ["ZapCorp Industries"]


def test_keyword_invoice_number_via_ilike(session, fake_embedder):
    _doc(session, fake_embedder, page_text="nothing relevant", supplier="Acme", number="INV-2026-0042")
    page = query("2026-0042", mode="keyword", session=session)
    assert any(r.invoice_number == "INV-2026-0042" for r in page.items)


def test_like_wildcards_are_escaped(session, fake_embedder):
    _doc(session, fake_embedder, page_text="plain text", supplier="Acme", number="A-1")
    assert query("%", mode="keyword", session=session).items == []
    assert query("_", mode="keyword", session=session).items == []


def test_keyword_snippet_highlights_match(session, fake_embedder):
    _doc(session, fake_embedder, page_text="the quarterly maintenance fee for pumps", supplier="Acme", number="A-1")
    page = query("maintenance", mode="keyword", session=session)
    hit = next(r for r in page.items if r.kind == "page")
    assert "<b>maintenance</b>" in hit.snippet


def test_semantic_excludes_unindexed_chunks(session, fake_embedder):
    _doc(session, fake_embedder, page_text="acme pumps", supplier="Acme", number="A-1", index=False)
    doc = session.query(Document).one()
    # chunks exist but were never embedded
    session.add(Chunk(document_id=doc.id, kind="page", page_no=1, text="acme pumps", indexed=False))
    session.commit()
    assert query("acme pumps", mode="semantic", session=session, embedder=fake_embedder).items == []


def test_semantic_ranks_relevant_first(session, fake_embedder):
    acme = _doc(session, fake_embedder, page_text="acme hydraulic pumps delivery", supplier="Acme", number="A-1")
    _doc(session, fake_embedder, page_text="office chairs and desks", supplier="Chairs Inc", number="C-1")
    page = query("hydraulic pumps", mode="semantic", session=session, embedder=fake_embedder)
    assert page.items[0].document_id == acme.id
    assert all(r.matched_by == "semantic" for r in page.items)


def test_hybrid_fuses_and_both_lists_outrank_one(session, fake_embedder):
    both = _doc(session, fake_embedder, page_text="titanium bolts order", supplier="Bolt Co", number="B-1")
    _doc(session, fake_embedder, page_text="unrelated paper supplies", supplier="Paper Co", number="P-1")
    page = query("titanium bolts", mode="hybrid", session=session, embedder=fake_embedder)

    top = page.items[0]
    assert top.document_id == both.id
    assert top.matched_by == "both"
    # one result per invoice/document key: fusion merged the two lists
    keys = [(r.document_id, r.invoice_id) for r in page.items]
    assert len(keys) == len(set(keys))
    singles = [r for r in page.items if r.matched_by != "both"]
    assert all(r.score < top.score for r in singles)


def test_status_filter_excludes_page_chunks_of_other_documents(session, fake_embedder):
    ok = _doc(session, fake_embedder, page_text="widgets", supplier="Ok Co", number="O-1", status="validated_ok")
    bad = _doc(session, fake_embedder, page_text="widgets", supplier="Bad Co", number="B-1", status="needs_review")
    page = query("widgets", mode="keyword", session=session, status="validated_ok")
    docs = {r.document_id for r in page.items}
    assert ok.id in docs
    assert bad.id not in docs


@pytest.mark.parametrize("filters, expected", [
    ({"supplier": "acme"}, {"A-1"}),
    ({"currency": "EUR"}, {"E-1"}),
    ({"date_from": date(2026, 6, 1)}, {"A-1", "E-1"}),
    ({"date_to": date(2026, 1, 31)}, {"O-1"}),
    ({"min_total": Decimal("150")}, {"E-1"}),
    ({"max_total": Decimal("60")}, {"O-1"}),
])
def test_invoice_filters(session, fake_embedder, filters, expected):
    _doc(session, fake_embedder, page_text="widgets", supplier="Acme GmbH", number="A-1",
         total="100.00", currency="USD", inv_date="2026-11-03")
    _doc(session, fake_embedder, page_text="widgets", supplier="Euro Ltd", number="E-1",
         total="200.00", currency="EUR", inv_date="2026-07-01")
    _doc(session, fake_embedder, page_text="widgets", supplier="Old Co", number="O-1",
         total="50.00", currency="USD", inv_date="2026-01-10")
    page = query("widgets", mode="keyword", session=session, **filters)
    numbers = {
        session.query(Invoice).filter_by(document_id=r.document_id).one().invoice_number
        for r in page.items
    }
    assert numbers == expected


def test_hybrid_degrades_to_keyword_when_embedding_fails(session, fake_embedder):
    _doc(session, fake_embedder, page_text="widgets", supplier="Acme", number="A-1")
    page = query("widgets", mode="hybrid", session=session, embedder=FailingEmbedder())
    assert page.degraded is True
    assert page.items and all(r.matched_by == "keyword" for r in page.items)


def test_semantic_raises_when_embedding_fails(session, fake_embedder):
    _doc(session, fake_embedder, page_text="widgets", supplier="Acme", number="A-1")
    with pytest.raises(ServiceError) as exc_info:
        query("widgets", mode="semantic", session=session, embedder=FailingEmbedder())
    assert exc_info.value.code == "EMBEDDING_UNAVAILABLE"


def test_pagination(session, fake_embedder):
    for i in range(5):
        _doc(session, fake_embedder, page_text="widgets", supplier=f"S{i}", number=f"N-{i}")
    first = query("widgets", mode="keyword", session=session, limit=3, offset=0)
    second = query("widgets", mode="keyword", session=session, limit=3, offset=3)
    assert first.total == second.total
    assert len(first.items) == 3
    assert _keys(first).isdisjoint(_keys(second))
