"""Indexing service: chunk documents and embed them with pgvector.

Failure leaves chunks with indexed=False; never changes document status.
A periodic Celery task (M8b) retries unindexed documents.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, DocumentPage, Invoice

if TYPE_CHECKING:
    from app.services.embedder import Embedder

_PAGE_CHUNK_SIZE = 1000   # characters
_PAGE_CHUNK_OVERLAP = 100


def _chunk_text(text: str, size: int = _PAGE_CHUNK_SIZE, overlap: int = _PAGE_CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks."""
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def _invoice_summary_text(inv: Invoice) -> str:
    parts = [
        inv.supplier_name or "",
        inv.invoice_number or "",
        str(inv.invoice_date) if inv.invoice_date else "",
        str(inv.total) if inv.total is not None else "",
        inv.currency or "",
    ]
    if inv.line_items:
        descs = [li.description for li in inv.line_items if li.description]
        parts.extend(descs[:10])
    return " | ".join(p for p in parts if p)


def index_document(
    document_id: uuid.UUID,
    session: Session,
    embedder: "Embedder",
) -> None:
    """Build text chunks, embed them, and store in the chunks table.

    On any embedding failure: logs the error, leaves chunks with indexed=False,
    and does NOT change the document's status.
    """
    doc = session.get(Document, document_id)
    if doc is None:
        raise ValueError(f"Document {document_id} not found")

    # Remove old chunks before re-indexing (idempotent)
    session.query(Chunk).filter_by(document_id=document_id).delete(synchronize_session=False)
    session.flush()

    # Build page chunks
    pages = (
        session.query(DocumentPage)
        .filter_by(document_id=document_id)
        .order_by(DocumentPage.page_no)
        .all()
    )
    page_chunks: list[Chunk] = []
    for page in pages:
        if not page.text:
            continue
        for part in _chunk_text(page.text):
            page_chunks.append(Chunk(
                document_id=document_id,
                invoice_id=None,
                kind="page",
                page_no=page.page_no,
                text=part,
                indexed=False,
            ))

    # Build invoice summary chunks
    invoices = session.query(Invoice).filter_by(document_id=document_id).all()
    summary_chunks: list[Chunk] = []
    for inv in invoices:
        summary_text = _invoice_summary_text(inv)
        if summary_text:
            summary_chunks.append(Chunk(
                document_id=document_id,
                invoice_id=inv.id,
                kind="invoice_summary",
                page_no=None,
                text=summary_text,
                indexed=False,
            ))

    all_chunks = page_chunks + summary_chunks
    if not all_chunks:
        return

    # Add all chunks to session (get ids via flush)
    for chunk in all_chunks:
        session.add(chunk)
    session.flush()

    # Embed in batches of 32
    texts = [c.text for c in all_chunks]
    try:
        vectors = embedder.embed(texts, task_type="RETRIEVAL_DOCUMENT")
        for chunk, vec in zip(all_chunks, vectors):
            chunk.embedding = vec
            chunk.indexed = True
    except Exception as exc:
        # Embedding failed: leave indexed=False, don't change document status
        import logging
        logging.getLogger(__name__).warning(
            "Embedding failed for document %s: %s", document_id, exc
        )
        # chunks remain with indexed=False

    session.flush()
