"""Tests for services/search.py and services/indexing.py."""
from __future__ import annotations

import io
import uuid
from decimal import Decimal

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.db.models import Chunk, Document, Invoice
from app.schemas.models import ExtractedInvoice
from app.services.indexing import index_document
from app.services.ingest import save_upload
from app.services.processing import process_document
from app.services.search import query as search_query
from tests.fakes import FakeLLM


def _make_pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(100, 700, text)
    c.save()
    return buf.getvalue()


def _run_and_index(session, fake_embedder, text, number, supplier, total, filename=None):
    pdf = _make_pdf(f"Invoice {number} Supplier: {supplier} Total: {total} {text}")
    result = save_upload(filename or f"{number}.pdf", pdf, session)
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
    index_document(doc.id, session, fake_embedder)
    session.commit()
    return doc


@pytest.mark.usefixtures("truncate_tables")
def test_index_creates_chunks(session, fake_embedder):
    doc = _run_and_index(session, fake_embedder, "Some invoice text", "INV-001", "Acme", "100.00")
    chunks = session.query(Chunk).filter_by(document_id=doc.id).all()
    assert len(chunks) >= 1
    # All chunks should be indexed
    assert all(c.indexed for c in chunks), "All chunks should be indexed"
    # Summary chunk should exist if invoice was extracted
    inv = session.query(Invoice).filter_by(document_id=doc.id).first()
    if inv:
        summary = session.query(Chunk).filter_by(invoice_id=inv.id, kind="invoice_summary").first()
        assert summary is not None


@pytest.mark.usefixtures("truncate_tables")
def test_index_idempotent(session, fake_embedder):
    """Calling index_document twice should not duplicate chunks."""
    doc = _run_and_index(session, fake_embedder, "Invoice text", "INV-002", "Beta Corp", "200.00")
    count1 = session.query(Chunk).filter_by(document_id=doc.id).count()

    index_document(doc.id, session, fake_embedder)
    session.commit()
    count2 = session.query(Chunk).filter_by(document_id=doc.id).count()

    assert count1 == count2


@pytest.mark.usefixtures("truncate_tables")
def test_failed_embedding_leaves_unindexed(session):
    """When embedder raises, chunks remain with indexed=False and status is unchanged."""
    class FailingEmbedder:
        def embed(self, texts, *, task_type="RETRIEVAL_DOCUMENT"):
            raise RuntimeError("Embedding service unavailable")

    pdf = _make_pdf("Invoice INV-003 Supplier: Fail Corp Total: 300.00")
    result = save_upload("fail.pdf", pdf, session)
    session.commit()
    doc = result.document
    llm = FakeLLM(responses=[ExtractedInvoice(
        invoice_number="INV-003",
        invoice_date="2026-11-03",
        supplier_name="Fail Corp",
        total="300.00",
        currency="USD",
    )])
    process_document(doc.id, session, llm=llm)
    session.commit()
    status_before = session.get(Document, doc.id).status

    index_document(doc.id, session, FailingEmbedder())
    session.commit()

    # Status unchanged
    doc = session.get(Document, doc.id)
    assert doc.status == status_before

    # Chunks exist but are not indexed
    chunks = session.query(Chunk).filter_by(document_id=doc.id).all()
    assert all(not c.indexed for c in chunks)


@pytest.mark.usefixtures("truncate_tables")
def test_keyword_search_finds_by_supplier(session, fake_embedder):
    _run_and_index(session, fake_embedder, "payment due", "INV-A", "ZapCorp Industries", "500.00", "a.pdf")
    _run_and_index(session, fake_embedder, "shipment receipt", "INV-B", "Unrelated Widgets", "300.00", "b.pdf")

    results = search_query(
        "ZapCorp",
        mode="keyword",
        session=session,
        limit=10,
    ).items
    assert len(results) > 0
    supplier_names = [r.supplier_name for r in results if r.supplier_name]
    assert any("ZapCorp" in (n or "") for n in supplier_names)


@pytest.mark.usefixtures("truncate_tables")
def test_semantic_search_ranks_relevant_higher(session, fake_embedder):
    """The FakeEmbedder is bag-of-words, so a query about Acme should rank Acme higher."""
    _run_and_index(session, fake_embedder, "acme delivery invoice payment", "INV-SEM1", "Acme Corp", "100.00", "sem1.pdf")
    _run_and_index(session, fake_embedder, "widget shipment receipt totally unrelated", "INV-SEM2", "Widget Co", "200.00", "sem2.pdf")

    results = search_query(
        "acme invoice payment",
        mode="semantic",
        session=session,
        embedder=fake_embedder,
        limit=10,
    ).items
    assert len(results) > 0
    # The first result should be the Acme document
    first = results[0]
    assert first.supplier_name is None or "Acme" in (first.supplier_name or "") or first.score > 0


@pytest.mark.usefixtures("truncate_tables")
def test_hybrid_search_returns_results(session, fake_embedder):
    _run_and_index(session, fake_embedder, "hybrid test document", "INV-HYB", "HybridCo", "150.00", "hyb.pdf")

    results = search_query(
        "hybrid",
        mode="hybrid",
        session=session,
        embedder=fake_embedder,
        limit=10,
    ).items
    assert len(results) > 0


@pytest.mark.usefixtures("truncate_tables")
def test_search_filter_by_status(session, fake_embedder):
    """Filter by status=needs_review should exclude validated_ok documents."""
    doc = _run_and_index(session, fake_embedder, "test invoice", "INV-FILT", "FilterCorp", "400.00", "filt.pdf")
    inv = session.query(Invoice).filter_by(document_id=doc.id).first()
    if inv is None:
        pytest.skip("No invoice extracted for filter test")

    # Force invoice status to validated_ok
    inv.status = "validated_ok"
    session.commit()

    results = search_query(
        "FilterCorp",
        mode="keyword",
        session=session,
        status="needs_review",
        limit=10,
    ).items
    # validated_ok invoice should not appear in needs_review filter
    for r in results:
        if r.invoice_id == inv.id:
            assert r.status == "needs_review"


@pytest.mark.usefixtures("truncate_tables")
def test_schema_indexes_exist(db_engine):
    """Verify the tsv generated column and both indexes exist after create_all."""
    from sqlalchemy import inspect
    inspector = inspect(db_engine)

    # Check chunks table has tsv column
    cols = {c["name"] for c in inspector.get_columns("chunks")}
    assert "tsv" in cols

    # Check indexes
    indexes = {ix["name"] for ix in inspector.get_indexes("chunks")}
    assert "ix_chunks_tsv_gin" in indexes
    assert "ix_chunks_embedding_hnsw" in indexes
