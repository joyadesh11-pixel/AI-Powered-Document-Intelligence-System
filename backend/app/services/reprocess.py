"""Reprocess service: re-queue a document for processing.

Eligible states: failed always; needs_review only with force=True.
Deletes previous invoices/pages/chunks (keeps processing_events history).
Bumps attempt counter; sets status=queued.
M8b Celery worker will call process_document when it picks it up.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, DocumentPage, Invoice


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ReprocessNotEligible(Exception):
    pass


def run(document_id: uuid.UUID, session: Session, *, force: bool = False) -> Document:
    """Re-queue document_id for reprocessing.

    Raises ReprocessNotEligible if the document is not in an eligible state.
    Returns the updated Document.
    """
    doc = session.get(Document, document_id)
    if doc is None:
        raise ValueError(f"Document {document_id} not found")

    eligible = doc.status == "failed" or (force and doc.status == "needs_review")
    if not eligible:
        raise ReprocessNotEligible(
            f"Document {document_id} has status={doc.status!r}; "
            "only 'failed' documents (or 'needs_review' with force=True) can be reprocessed"
        )

    # Delete previous result data; keep processing_events for audit
    session.query(Invoice).filter_by(document_id=document_id).delete(synchronize_session=False)
    session.query(DocumentPage).filter_by(document_id=document_id).delete(synchronize_session=False)
    session.query(Chunk).filter_by(document_id=document_id).delete(synchronize_session=False)

    doc.attempt += 1
    doc.status = "queued"
    doc.error = None
    doc.error_code = None
    doc.current_node = None
    doc.finished_at = None
    doc.updated_at = _now()

    session.flush()
    return doc
