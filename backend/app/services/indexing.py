"""Indexing service: chunk documents and embed them with pgvector.

Chunks are written first (indexed=False), then embedded in batches of
INDEX_BATCH_SIZE.  A failing batch leaves only its own chunks unindexed;
embed_pending() (periodic task, corrections) retries them later.
Never reads or changes document status.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, DocumentPage, Invoice
from app.pipeline.config import get_config

if TYPE_CHECKING:
    from app.services.embedder import Embedder

log = logging.getLogger(__name__)


def _chunk_text(text: str, size: int, overlap: int) -> list[str]:
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


def invoice_summary_text(inv: Invoice) -> str:
    """Supplier, number, date, total, currency, line descriptions (docs/06)."""
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


def summary_chunk(inv: Invoice) -> Chunk | None:
    text = invoice_summary_text(inv)
    if not text:
        return None
    return Chunk(document_id=inv.document_id, invoice_id=inv.id, kind="invoice_summary",
                 page_no=None, text=text, indexed=False)


def _embed_chunks(chunks: list[Chunk], embedder: "Embedder", label: object) -> int:
    """Embed chunks batch by batch; return how many were indexed."""
    cfg = get_config()
    batch_size, dim = cfg.INDEX_BATCH_SIZE, cfg.EMBED_DIM
    done = 0
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i: i + batch_size]
        try:
            vectors = embedder.embed([c.text for c in batch], task_type="RETRIEVAL_DOCUMENT")
        except Exception as exc:
            # leave this batch indexed=False; embed_pending retries it later
            log.warning("Embedding batch %d failed for %s: %s", i // batch_size, label, type(exc).__name__)
            continue
        if len(vectors) != len(batch) or any(len(v) != dim for v in vectors):
            # a vector of the wrong size would fail the whole flush; treat it like a failed batch
            log.warning("Embedding batch %d for %s has wrong shape (expected dim %d)",
                        i // batch_size, label, dim)
            continue
        for chunk, vec in zip(batch, vectors):
            chunk.embedding = vec
            chunk.indexed = True
        done += len(batch)
    return done


def index_document(
    document_id: uuid.UUID,
    session: Session,
    embedder: "Embedder",
) -> int:
    """Rebuild a document's chunks and embed them; return the number indexed.

    Idempotent: previous chunks are replaced.  Embedding failures are logged
    and leave the affected chunks with indexed=False; nothing is raised.
    """
    cfg = get_config()
    doc = session.get(Document, document_id)
    if doc is None:
        raise ValueError(f"Document {document_id} not found")

    session.query(Chunk).filter_by(document_id=document_id).delete(synchronize_session=False)
    session.flush()

    chunks: list[Chunk] = []
    pages = (
        session.query(DocumentPage)
        .filter_by(document_id=document_id)
        .order_by(DocumentPage.page_no)
        .all()
    )
    for page in pages:
        for part in _chunk_text(page.text or "", cfg.CHUNK_CHARS, cfg.CHUNK_OVERLAP):
            chunks.append(Chunk(document_id=document_id, invoice_id=None, kind="page",
                                page_no=page.page_no, text=part, indexed=False))

    for inv in session.query(Invoice).filter_by(document_id=document_id).all():
        chunk = summary_chunk(inv)
        if chunk is not None:
            chunks.append(chunk)

    if not chunks:
        return 0
    session.add_all(chunks)
    session.flush()

    done = _embed_chunks(chunks, embedder, document_id)
    session.flush()
    return done


def embed_pending(session: Session, embedder: "Embedder", limit: int = 1000) -> int:
    """Embed up to `limit` chunks still marked indexed=False; return how many succeeded."""
    pending = (
        session.query(Chunk)
        .filter(Chunk.indexed.is_(False))
        .order_by(Chunk.document_id, Chunk.page_no)
        .limit(limit)
        .all()
    )
    if not pending:
        return 0
    done = _embed_chunks(pending, embedder, "pending chunks")
    session.flush()
    return done
